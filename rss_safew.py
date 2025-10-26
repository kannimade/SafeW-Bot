import feedparser
import logging
import asyncio
import json
import os
import aiohttp

# 环境变量配置（需更新SAFEW_CHAT_ID为10000294405）
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")  # 需在Secrets中更新为10000294405
RSS_URL = os.getenv("RSS_FEED_URL")
POSTS_FILE = "sent_posts.json"
MAX_PUSH_PER_RUN = 5  # 单次运行最多推送5条，避免刷屏

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 读取已发送ID（去重核心：仅记录推送到当前群组的内容）
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

# 保存已发送ID（仅保存推送到当前群组的内容）
def save_sent_posts(post_ids):
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_ids, f, ensure_ascii=False, indent=2)
        logging.info(f"已保存{len(post_ids)}条推送到群组{SAFEW_CHAT_ID}的记录")
    except Exception as e:
        logging.error(f"保存已发送ID失败：{str(e)}")

# 获取RSS更新（新增RSS条目去重，避免源本身重复）
def fetch_updates():
    try:
        logging.info(f"获取RSS源：{RSS_URL}")
        feed = feedparser.parse(RSS_URL)
        if feed.bozo:
            logging.error(f"RSS解析错误：{feed.bozo_exception}")
            return None
        
        # 新增：按链接去重（避免RSS源本身有重复条目）
        unique_entries = []
        seen_links = set()
        for entry in feed.entries:
            link = entry.get("link", "")
            if link and link not in seen_links:
                seen_links.add(link)
                unique_entries.append(entry)
        
        logging.info(f"成功获取{len(feed.entries)}条条目，去重后剩余{len(unique_entries)}条有效条目")
        return unique_entries
    except Exception as e:
        logging.error(f"获取RSS失败：{str(e)}")
        return None

# 转义Markdown特殊字符（适配格式）
def escape_markdown(text):
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        text = text.replace(char, f"\{char}")
    return text

# 发送消息到SafeW（适配新格式+限制频率）
async def send_message(session, title, author, link, delay=5):
    try:
        await asyncio.sleep(delay)  # 延长间隔到5秒，避免频率限制
        # 1. 按要求构造格式：标题\n由 @昵称 发起的话题讨论\n链接：xxx
        escaped_title = escape_markdown(title)
        escaped_author = escape_markdown(author)
        escaped_link = escape_markdown(link)
        message = f"{escaped_title}\n由 @{escaped_author} 发起的话题讨论\n链接：{escaped_link}"
        logging.info(f"准备发送消息：\n{message[:100]}...")
        
        # 2. API地址（保持正确格式）
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",
            "disable_notification": False,  # 可选：True为静默推送
            "disable_web_page_preview": True
        }
        
        async with session.post(api_url, json=payload) as response:
            response_text = await response.text() or "无响应内容"
            if response.status == 200:
                logging.info("✅ 消息发送成功")
                return True
            else:
                logging.error(f"❌ 发送失败：状态码{response.status}，响应{response_text[:200]}")
                return False
    except Exception as e:
        logging.error(f"❌ 发送过程异常：{str(e)}")
        return False

# 检查更新并推送（修复重复+限制数量）
async def check_for_updates():
    # 1. 读取已推送记录（仅当前群组）
    sent_post_ids = load_sent_posts()
    # 2. 获取去重后的RSS条目
    rss_entries = fetch_updates()
    if not rss_entries:
        return

    # 3. 筛选未推送的条目（按link去重，而非之前的guid，更可靠）
    new_entries = []
    for entry in rss_entries:
        link = entry.get("link", "")
        title = entry.get("title", "无标题")
        # 提取作者（昵称）：优先取author，无则用"未知用户"（需根据RSS源字段调整）
        author = entry.get("author", entry.get("dc_author", "未知用户"))
        if link and link not in sent_post_ids:
            new_entries.append({"title": title, "author": author, "link": link})

    if not new_entries:
        logging.info("无新内容需要推送")
        return

    # 4. 限制单次推送数量（最多推MAX_PUSH_PER_RUN条）
    push_entries = new_entries[:MAX_PUSH_PER_RUN]
    logging.info(f"发现{len(new_entries)}条新内容，本次推送前{len(push_entries)}条（单次最多{MAX_PUSH_PER_RUN}条）")
    
    # 5. 推送新内容
    async with aiohttp.ClientSession() as session:
        success_links = []
        for i, entry in enumerate(push_entries):
            # 第一条延迟0秒，后续每条延迟5秒
            delay = 5 if i > 0 else 0
            if await send_message(
                session,
                title=entry["title"],
                author=entry["author"],
                link=entry["link"],
                delay=delay
            ):
                success_links.append(entry["link"])  # 用link作为去重标识，更可靠

    # 6. 更新已推送记录（仅保存成功推送的link）
    if success_links:
        sent_post_ids.extend(success_links)
        save_sent_posts(sent_post_ids)
    else:
        logging.info("无成功推送的内容，不更新记录")

# 主函数（增加配置校验）
async def main():
    logging.info("===== SafeW RSS推送脚本开始运行 =====")
    # 1. 校验核心配置
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("⚠️ Token格式错误！应为 数字:字符 格式")
        return
    if SAFEW_CHAT_ID != "10000294405":
        logging.warning(f"⚠️ 群组ID可能错误！当前为{SAFEW_CHAT_ID}，请确认是否为目标群组ID 10000294405")
    
    # 2. 执行推送逻辑
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"主逻辑执行失败：{str(e)}")
    logging.info("===== 脚本运行结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
