import feedparser
import logging
import asyncio
import json
import os
import aiohttp  # 替换telegram库，用于SafeW API请求

# 从环境变量读取SafeW配置（替换原Telegram配置）
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")
RSS_URL = os.getenv("RSS_URL")

# 存储已发送ID的仓库文件（与原方案一致）
POSTS_FILE = "sent_posts.json"

# 配置日志（保持原格式）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 读取已发送的post_id（逻辑完全复用）
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

# 保存已发送的post_id（逻辑完全复用）
def save_sent_posts(post_ids):
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_ids, f, ensure_ascii=False, indent=2)
        logging.info(f"已保存ID列表（共{len(post_ids)}条）")
    except Exception as e:
        logging.error(f"保存已发送ID失败：{str(e)}")

# 获取RSS更新（逻辑完全复用）
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

# 转义Markdown特殊字符（适配SafeW格式）
def escape_markdown(text):
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        text = text.replace(char, f"\{char}")
    return text

# 发送单条消息到SafeW（替换原Telegram发送逻辑）
async def send_message(session, title, link, delay=3):
    try:
        # 发送前等待（避免频率限制，与原方案一致）
        await asyncio.sleep(delay)
        escaped_title = escape_markdown(title)
        escaped_link = escape_markdown(link)
        # 适配SafeW的消息格式（简化为清晰结构）
        message = f"🔔 RSS新内容提醒\n`{escaped_title}`\n{escaped_link}"
        logging.info(f"发送消息：{message[:100]}")
        
        # SafeW Bot API请求（替换Telegram API）
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        params = {
            "chat_id": SAFEW_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown",  # SafeW通常支持基础Markdown
            "disable_web_page_preview": True
        }
        
        # 异步发送请求（替代telegram库的Bot实例）
        async with session.get(api_url, params=params) as response:
            response_text = await response.text()
            logging.info(f"SafeW响应：{response_text[:200]}")
            
            if response.status == 200:
                logging.info("消息发送成功")
                return True
            else:
                logging.error(f"SafeW发送失败：状态码{response.status}，响应{response_text}")
                return False
    except Exception as e:
        logging.error(f"发送过程异常：{str(e)}")
        return False

# 检查更新并推送所有新帖子（仅修改发送逻辑调用）
async def check_for_updates(sent_post_ids):
    updates = fetch_updates()
    if not updates:
        return

    new_posts = []
    for entry in updates.entries:
        try:
            # 提取帖子ID（完全复用原适配逻辑，确保兼容性）
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
        # 保持原排序逻辑（从旧到新推送）
        new_posts.sort(key=lambda x: int(x[0]))
        logging.info(f"发现{len(new_posts)}条新帖子，准备依次推送（间隔3秒）")
        
        # 用aiohttp ClientSession替代Telegram Bot实例
        async with aiohttp.ClientSession() as session:
            for i, (post_id, title, link) in enumerate(new_posts):
                delay = 3 if i > 0 else 0
                # 调用SafeW发送函数
                success = await send_message(session, title, link, delay)
                if success:
                    sent_post_ids.append(post_id)

        # 保存更新（完全复用原逻辑）
        save_sent_posts(sent_post_ids)
    else:
        logging.info("无新帖子需要推送")

# 主函数（仅修改发送逻辑调用）
async def main():
    logging.info("===== SafeW RSS推送脚本开始运行 =====")
    sent_post_ids = load_sent_posts()
    try:
        await check_for_updates(sent_post_ids)
    except Exception as e:
        logging.error(f"主逻辑执行失败：{str(e)}")
    logging.info("===== 脚本运行结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
