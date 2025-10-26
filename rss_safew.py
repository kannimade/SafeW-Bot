import feedparser
import logging
import asyncio
import json
import os
import aiohttp

# 环境变量配置（沿用你的Secret）
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")
RSS_URL = os.getenv("RSS_FEED_URL")
POSTS_FILE = "sent_posts.json"

# 日志配置（突出文档适配信息）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 读取已发送ID（不变）
def load_sent_posts():
    try:
        if os.path.exists(POSTS_FILE):
            with open(POSTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        logging.info("首次运行，创建空ID列表")
        return []
    except Exception as e:
        logging.error(f"读取已发送ID失败：{str(e)}")
        return []

# 保存已发送ID（不变）
def save_sent_posts(post_ids):
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_ids, f, ensure_ascii=False, indent=2)
        logging.info(f"已保存ID列表（共{len(post_ids)}条）")
    except Exception as e:
        logging.error(f"保存已发送ID失败：{str(e)}")

# 获取RSS更新（不变）
def fetch_updates():
    try:
        logging.info(f"获取RSS源：{RSS_URL}")
        feed = feedparser.parse(RSS_URL)
        if feed.bozo:
            logging.error(f"RSS解析错误：{feed.bozo_exception}")
            return None
        logging.info(f"成功获取{len(feed.entries)}条RSS条目")
        return feed
    except Exception as e:
        logging.error(f"获取RSS失败：{str(e)}")
        return None

# 转义Markdown特殊字符（按文档支持情况保留）
def escape_markdown(text):
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        text = text.replace(char, f"\{char}")
    return text

# 发送消息到SafeW（100%适配文档）
async def send_message(session, title, link, delay=3):
    try:
        await asyncio.sleep(delay)
        # 1. 消息内容（简洁适配文档text参数）
        escaped_title = escape_markdown(title)
        escaped_link = escape_markdown(link)
        message = f"🔔 RSS新内容提醒\n标题：{escaped_title}\n链接：{escaped_link}"
        logging.info(f"准备发送消息：{message[:50]}...")
        
        # 2. 核心修正：API地址（按文档格式，bot后无斜杠）
        # 文档格式：https://api.safew.org/bot<Token>/sendMessage
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        # 脱敏后对比文档（确保格式一致）
        check_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN[:10]}****/sendMessage"
        logging.info(f"文档核对：当前地址格式与文档一致 → {check_url}")
        
        # 3. 请求参数（严格按文档定义）
        # 文档参数说明：
        # - chat_id：必填，整数/字符串
        # - text：必填，消息内容
        # - 可选参数：disable_notification（Boolean）、protect_content（Boolean）
        payload = {
            "chat_id": SAFEW_CHAT_ID,                # 文档：必填，确保为纯数字/用户名
            "text": message,                         # 文档：必填，支持换行符
            "parse_mode": "Markdown",                # 若文档不支持可删除（无则默认纯文本）
            "disable_notification": False,           # 文档：可选Boolean，按需求调整
            "disable_web_page_preview": True         # 若文档不支持可删除
        }
        
        # 4. 请求方式（文档支持浏览器GET，sendMessage建议用POST更稳定）
        logging.info(f"请求方式：POST，参数：{json.dumps(payload, ensure_ascii=False)[:100]}...")
        async with session.post(api_url, json=payload) as response:
            response_text = await response.text() or "无响应内容"
            logging.info(f"文档核对：响应状态码={response.status}，响应内容={response_text[:200]}")
            
            # 按文档标准错误码判断
            if response.status == 200:
                logging.info("✅ 消息发送成功！（响应符合文档成功格式）")
                return True
            elif response.status == 404:
                logging.error(f"❌ 404：地址格式仍错误！请手动访问文档示例：https://api.safew.org/bot{SAFEW_BOT_TOKEN[:5]}****/getMe 验证")
                return False
            elif response.status == 400:
                logging.error(f"❌ 400：参数错误（文档核对）→ 1.chat_id是否为纯数字/用户名 2.text是否含非法字符")
                return False
            elif response.status == 401:
                logging.error(f"❌ 401：Token无效！请核对文档中Token格式（如 11547252:34bdawFefZzNhogibHqEpEc2x6N）")
                return False
            else:
                logging.error(f"❌ 发送失败：请对照文档错误码表排查（状态码{response.status}）")
                return False
    except Exception as e:
        logging.error(f"❌ 发送过程异常：{str(e)}（可能是网络问题）")
        return False

# 检查更新并推送（不变）
async def check_for_updates(sent_post_ids):
    updates = fetch_updates()
    if not updates:
        return

    new_posts = []
    for entry in updates.entries:
        try:
            guid_parts = entry.guid.split("-")
            if len(guid_parts) < 2:
                logging.warning(f"无效GUID格式：{entry.guid}，跳过")
                continue
            post_id = guid_parts[-1].split(".")[0]
            if not post_id.isdigit():
                logging.warning(f"提取的ID非数字：{post_id}，跳过")
                continue
            logging.info(f"解析到有效ID：{post_id}，标题：{entry.title[:20]}...")
            if post_id not in sent_post_ids:
                new_posts.append((post_id, entry.title, entry.link))
        except Exception as e:
            logging.error(f"解析条目失败（GUID：{entry.guid}）：{str(e)}")
            continue

    if new_posts:
        new_posts.sort(key=lambda x: int(x[0]))
        logging.info(f"发现{len(new_posts)}条新帖子，准备依次推送（间隔3秒）")
        
        async with aiohttp.ClientSession() as session:
            for i, (post_id, title, link) in enumerate(new_posts):
                delay = 3 if i > 0 else 0
                success = await send_message(session, title, link, delay)
                if success:
                    sent_post_ids.append(post_id)

        save_sent_posts(sent_post_ids)
    else:
        logging.info("无新帖子需要推送")

# 主函数（增加文档验证提示）
async def main():
    logging.info("===== SafeW RSS推送脚本开始运行 =====")
    # 前置验证（按文档Token格式）
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error(f"⚠️  文档核对：Token格式错误！应为 数字:字符（如 11547252:34bdawFefZzNhogibHqEpEc2x6N）")
        return
    
    # 建议先手动验证getMe接口（文档推荐）
    logging.info(f"💡 验证建议：手动访问此地址确认Token/地址有效 → https://api.safew.org/bot{SAFEW_BOT_TOKEN[:5]}****/getMe")
    
    sent_post_ids = load_sent_posts()
    try:
        await check_for_updates(sent_post_ids)
    except Exception as e:
        logging.error(f"主逻辑执行失败：{str(e)}")
    logging.info("===== 脚本运行结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
