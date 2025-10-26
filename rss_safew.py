import feedparser
import logging
import asyncio
import json
import os
import aiohttp
from bs4 import BeautifulSoup

# 环境变量配置（更新域名相关）
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")  # 目标群组ID：10000294405
RSS_URL = os.getenv("RSS_FEED_URL")  # 若RSS源也变更，需在Secrets中更新
POSTS_FILE = "sent_posts.json"
MAX_PUSH_PER_RUN = 5  # 单次最多推5条
FIXED_PROJECT_URL = "https://tyw29.cc/"  # 项目地址更新为新域名
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36"

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 读取已发送记录（不变）
def load_sent_posts():
    try:
        if os.path.exists(POSTS_FILE):
            with open(POSTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        logging.info("首次运行，创建空记录列表")
        return []
    except Exception as e:
        logging.error(f"读取已发送记录失败：{str(e)}")
        return []

# 保存已发送记录（不变）
def save_sent_posts(post_links):
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_links, f, ensure_ascii=False, indent=2)
        logging.info(f"已保存{len(post_links)}条推送到群组{SAFEW_CHAT_ID}的记录")
    except Exception as e:
        logging.error(f"保存已发送记录失败：{str(e)}")

# 获取RSS并去重（不变，若RSS源域名变更，需在Secrets中更新RSS_FEED_URL）
def fetch_updates():
    try:
        logging.info(f"获取RSS源：{RSS_URL}")
        feed = feedparser.parse(RSS_URL)
        if feed.bozo:
            logging.error(f"RSS解析错误：{feed.bozo_exception}")
            return None
        
        # 按link去重
        unique_entries = []
        seen_links = set()
        for entry in feed.entries:
            link = entry.get("link", "")
            if link and link not in seen_links:
                seen_links.add(link)
                unique_entries.append(entry)
        
        logging.info(f"原始条目{len(feed.entries)}条，去重后剩余{len(unique_entries)}条有效条目")
        return unique_entries
    except Exception as e:
        logging.error(f"获取RSS失败：{str(e)}")
        return None

# 异步爬取网页HTML并提取目标div中的图片（适配新域名，动态处理）
async def get_images_from_webpage(session, webpage_url):
    try:
        # 爬取网页HTML（新域名自动适配）
        headers = {"User-Agent": USER_AGENT}
        async with session.get(webpage_url, headers=headers, timeout=10) as resp:
            if resp.status != 200:
                logging.warning(f"网页请求失败（{resp.status}）：{webpage_url}")
                return []
            html = await resp.text()
        
        # 解析HTML，提取目标div中的图片（div选择器不变）
        soup = BeautifulSoup(html, "html.parser")
        target_div = soup.find("div", class_="message break-all", isfirst="1")
        if not target_div:
            logging.warning(f"未找到目标div：{webpage_url}")
            return []
        
        # 提取图片链接（自动适配新域名的相对路径）
        img_tags = target_div.find_all("img")
        if not img_tags:
            logging.info(f"目标div中无图片：{webpage_url}")
            return []
        
        images = []
        for img in img_tags:
            img_src = img.get("src", "").strip()
            if img_src:
                # 相对路径自动拼接新域名（如"/uploads/123.jpg" → "https://tyw29.cc/uploads/123.jpg"）
                if img_src.startswith("/"):
                    domain = "/".join(webpage_url.split("/")[:3])  # 动态提取当前网页域名
                    img_src = f"{domain}{img_src}"
                images.append(img_src)
        
        logging.info(f"从{webpage_url}提取到{len(images)}张图片")
        return images
    except Exception as e:
        logging.error(f"提取图片失败（{webpage_url}）：{str(e)}")
        return []

# 转义Markdown特殊字符（不变）
def escape_markdown(text):
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        text = text.replace(char, f"\{char}")
    return text

# 发送图片消息（不变，自动适配新域名图片链接）
async def send_photo(session, image_url, caption, delay=5):
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "Markdown",
            "disable_notification": False
        }
        
        async with session.post(api_url, json=payload) as response:
            response_text = await response.text() or "无响应内容"
            if response.status == 200:
                logging.info(f"✅ 图片发送成功：{image_url[:50]}...")
                return True
            else:
                logging.error(f"❌ 图片发送失败（{image_url[:50]}）：状态码{response.status}，响应{response_text[:200]}")
                return False
    except Exception as e:
        logging.error(f"❌ 图片发送异常（{image_url[:50]}）：{str(e)}")
        return False

# 发送纯文本消息（核心修改：移除【图片】占位符）
async def send_text(session, caption, delay=5):
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_notification": False,
            "disable_web_page_preview": True
        }
        
        async with session.post(api_url, json=payload) as response:
            if response.status == 200:
                logging.info("✅ 文本消息发送成功")
                return True
            else:
                logging.error(f"❌ 文本发送失败：状态码{response.status}")
                return False
    except Exception as e:
        logging.error(f"❌ 文本发送异常：{str(e)}")
        return False

# 检查更新并推送（核心修改：文本格式移除占位符）
async def check_for_updates():
    sent_links = load_sent_posts()
    rss_entries = fetch_updates()
    if not rss_entries:
        return

    new_entries = []
    for entry in rss_entries:
        link = entry.get("link", "")
        title = entry.get("title", "无标题")
        author = entry.get("author", entry.get("dc_author", "未知用户"))
        if link and link not in sent_links:
            new_entries.append({"title": title, "author": author, "link": link})

    if not new_entries:
        logging.info("无新内容需要推送")
        return

    # 限制单次推送数量
    push_entries = new_entries[:MAX_PUSH_PER_RUN]
    logging.info(f"发现{len(new_entries)}条新内容，本次推送前{len(push_entries)}条")
    
    async with aiohttp.ClientSession() as session:
        success_links = []
        for i, entry in enumerate(push_entries):
            # 1. 构造文本内容（移除【图片】占位符，项目地址更新为新域名）
            title = escape_markdown(entry["title"])
            author = escape_markdown(entry["author"])
            link = escape_markdown(entry["link"])
            caption = (
                f"{title}\n"
                f"由 @{author} 发起的话题讨论\n"
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"  # 新域名项目地址
            )
            
            # 2. 从网页提取图片（新域名自动适配）
            images = await get_images_from_webpage(session, entry["link"])
            
            # 3. 发送（有图发图，无图发纯文本，无占位符）
            delay = 5 if i > 0 else 0
            send_success = False
            
            if images:
                # 发送第一张图片+文本说明
                send_success = await send_photo(session, images[0], caption, delay)
            else:
                # 无图时直接发送文本（无【图片】占位符）
                send_success = await send_text(session, caption, delay)
            
            if send_success:
                success_links.append(entry["link"])

    # 更新已发送记录
    if success_links:
        sent_links.extend(success_links)
        save_sent_posts(sent_links)
    else:
        logging.info("无成功推送的内容，不更新记录")

# 主函数（适配新域名校验）
async def main():
    logging.info("===== SafeW RSS推送脚本开始运行 =====")
    # 校验配置
    if SAFEW_CHAT_ID != "10000294405":
        logging.warning(f"⚠️  群组ID为{SAFEW_CHAT_ID}，请确认是否为目标群组10000294405")
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("⚠️  Token格式错误！应为 数字:字符 格式")
        return
    # 提示新域名相关
    logging.info(f"当前项目地址：{FIXED_PROJECT_URL}")
    
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"主逻辑执行失败：{str(e)}")
    logging.info("===== 脚本运行结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
