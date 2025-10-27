import feedparser
import logging
import asyncio
import json
import os
import aiohttp
import uuid
from bs4 import BeautifulSoup

# ====================== 环境配置（不变）======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")
RSS_FEED_URL = os.getenv("RSS_FEED_URL")
POSTS_FILE = "sent_posts.json"
MAX_PUSH_PER_RUN = 5
FIXED_PROJECT_URL = "https://tyw29.cc/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# ====================== 日志配置（不变）======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 去重记录管理（不变）======================
def load_sent_posts():
    try:
        if os.path.exists(POSTS_FILE):
            with open(POSTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        logging.info("首次运行，初始化空去重记录")
        return []
    except json.JSONDecodeError:
        logging.error("❌ sent_posts.json格式错误，重置为空列表")
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    except Exception as e:
        logging.error(f"❌ 读取去重记录失败：{str(e)}")
        return []

def save_sent_posts(post_links):
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_links, f, ensure_ascii=False, indent=2)
        logging.info(f"✅ 已保存{len(post_links)}条去重记录")
    except Exception as e:
        logging.error(f"❌ 保存去重记录失败：{str(e)}")

# ====================== RSS获取与去重（不变）======================
def fetch_updates():
    try:
        logging.info(f"正在获取RSS源：{RSS_FEED_URL}")
        feed = feedparser.parse(RSS_FEED_URL)
        if feed.bozo:
            logging.error(f"❌ RSS解析失败：{feed.bozo_exception}")
            return None
        
        unique_entries = []
        seen_links = set()
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if link and link not in seen_links:
                seen_links.add(link)
                unique_entries.append(entry)
        
        logging.info(f"✅ RSS源去重后剩余{len(unique_entries)}条")
        return unique_entries
    except Exception as e:
        logging.error(f"❌ 获取RSS源异常：{str(e)}")
        return None

# ====================== 网页图片提取（不变）======================
async def get_images_from_webpage(session, webpage_url):
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": FIXED_PROJECT_URL
        }
        
        async with session.get(webpage_url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                logging.warning(f"⚠️ 帖子请求失败（{resp.status}）：{webpage_url}")
                return []
            html = await resp.text()
        
        soup = BeautifulSoup(html, "html.parser")
        target_divs = soup.find_all("div", class_="message break-all", isfirst="1")
        logging.info(f"找到目标div数量：{len(target_divs)}")
        if not target_divs:
            return []
        
        images = []
        base_domain = "/".join(webpage_url.split("/")[:3])
        for div in target_divs:
            img_tags = div.find_all("img")
            logging.info(f"目标div中找到{len(img_tags)}个img标签")
            
            for img in img_tags:
                img_url = img.get("data-src", "").strip() or img.get("src", "").strip()
                if not img_url or img_url.startswith(("data:image/", "javascript:")):
                    continue
                
                if img_url.startswith("/"):
                    img_url = f"{base_domain}{img_url}"
                elif not img_url.startswith(("http://", "https://")):
                    img_url = f"{base_domain}/{img_url}"
                
                if img_url.startswith(("http://", "https://")) and img_url not in images:
                    images.append(img_url)
                    logging.info(f"✅ 提取到图片：{img_url[:60]}...")
        
        if images:
            logging.info(f"从{webpage_url}成功提取{len(images)}张图片")
            return images[:1]
        else:
            logging.warning(f"⚠️ 未提取到有效图片：{webpage_url}")
            return []
    except Exception as e:
        logging.error(f"❌ 提取图片异常：{str(e)}")
        return []

# ====================== Markdown特殊字符转义（核心修复：移除.和-的转义）======================
def escape_markdown(text):
    """
    仅转义影响Markdown格式的字符，不转义URL中的.和-
    需转义字符：_ * ~ ` > # + ! ( ) （避免文本被解析为Markdown格式）
    不转义字符：. - （URL合法字符，转义后破坏链接）
    """
    special_chars = r"_*~`>#+!()"  # 移除了原有的.和-
    for char in special_chars:
        if char in text:
            text = text.replace(char, f"\{char}")
    return text

# ====================== 图片+文字合并发送（不变）=======================
async def send_photo_with_caption(session, image_url, caption, delay=5):
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendPhoto"
        logging.info(f"\n=== 开始处理带文字的图片 ===")
        logging.info(f"图片URL：{image_url[:60]}...")
        logging.info(f"文字内容：{caption[:60]}...")

        # 1. 下载图片二进制数据
        try:
            img_headers = {
                "User-Agent": USER_AGENT,
                "Referer": FIXED_PROJECT_URL
            }
            async with session.get(
                image_url,
                headers=img_headers,
                timeout=15,
                ssl=False
            ) as img_resp:
                if img_resp.status != 200:
                    logging.error(f"❌ 图片下载失败（{img_resp.status}）")
                    return False
                img_data = await img_resp.read()
                img_content_type = img_resp.headers.get("Content-Type", "image/jpeg")
                img_size = len(img_data)
                logging.info(f"✅ 图片下载完成：{img_size}字节，{img_content_type}")

                if img_size > 10 * 1024 * 1024:
                    logging.error(f"❌ 图片超过10MB限制")
                    return False
        except Exception as e:
            logging.error(f"❌ 图片下载异常：{str(e)}")
            return False

        # 2. 生成boundary
        try:
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
            logging.info(f"生成boundary：{boundary}")
        except Exception as e:
            logging.error(f"❌ 生成boundary失败：{str(e)}")
            return False

        # 3. 构造请求体（含caption字段）
        try:
            chat_id_str = str(SAFEW_CHAT_ID)
            filename = image_url.split("/")[-1].split("?")[0].replace('"', '').replace("'", "").replace(" ", "_")
            logging.info(f"文件名：{filename}")

            # 构造文本字段（chat_id + caption）
            text_parts = [
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n'
                f"\r\n"
                f"{chat_id_str}\r\n",
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="caption"\r\n'
                f"\r\n"
                f"{caption}\r\n"
            ]
            text_part = "".join(text_parts).encode("utf-8")

            # 构造图片字段
            file_part_header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
                f"Content-Type: {img_content_type}\r\n"
                f"\r\n"
            ).encode("utf-8")

            end_part = f"\r\n--{boundary}--\r\n".encode("utf-8")
            body = text_part + file_part_header + img_data + end_part
            logging.info(f"✅ 请求体构造完成（含文字，大小：{len(body)}字节）")
        except Exception as e:
            logging.error(f"❌ 构造请求体失败：{str(e)}")
            return False

        # 4. 发送请求
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
            "Content-Length": str(len(body))
        }
        try:
            async with session.post(
                api_url,
                data=body,
                headers=headers,
                timeout=20,
                ssl=False
            ) as response:
                response_text = await response.text(encoding="utf-8", errors="replace")
                logging.info(f"响应：状态码{response.status}，内容：{response_text[:150]}...")

                if response.status == 200:
                    logging.info(f"✅ 图片+文字合并发送成功！")
                    return True
                else:
                    logging.error(f"❌ 发送失败（{response.status}）：{response_text}")
                    return False
        except Exception as e:
            logging.error(f"❌ 发送请求异常：{str(e)}")
            return False

    except Exception as e:
        logging.error(f"❌ 图片+文字发送总异常：{str(e)}")
        return False

# ====================== 纯文本发送（不变）=======================
async def send_text(session, caption, delay=5):
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        
        async with session.post(api_url, json=payload, timeout=15) as response:
            if response.status == 200:
                logging.info("✅ 纯文本发送成功")
                return True
            else:
                response_text = await response.text() or "无响应内容"
                logging.error(f"❌ 纯文本发送失败（{response.status}）：{response_text}")
                return False
    except Exception as e:
        logging.error(f"❌ 纯文本发送异常：{str(e)}")
        return False

# ====================== 核心推送逻辑（不变）======================
async def check_for_updates():
    sent_links = load_sent_posts()
    rss_entries = fetch_updates()
    if not rss_entries:
        logging.info("无有效RSS内容，结束推送")
        return

    new_entries = []
    for entry in rss_entries:
        link = entry.get("link", "").strip()
        title = entry.get("title", "无标题").strip()
        author = entry.get("author", entry.get("dc_author", "未知用户")).strip()
        
        if link and link not in sent_links:
            new_entries.append({"title": title, "author": author, "link": link})

    if not new_entries:
        logging.info("无新内容需要推送")
        return

    push_entries = new_entries[:MAX_PUSH_PER_RUN]
    logging.info(f"发现{len(new_entries)}条新内容，推送前{len(push_entries)}条")

    async with aiohttp.ClientSession() as session:
        success_links = []
        for i, entry in enumerate(push_entries):
            # 构造文字内容（仅转义格式字符，URL正常显示）
            title = escape_markdown(entry["title"])
            author = escape_markdown(entry["author"])
            link = entry["link"]  # URL无需转义，直接使用原链接
            caption = (
                f"{title}\n"
                f"由 @{author} 发起的话题讨论\n"
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"
            )
            
            images = await get_images_from_webpage(session, entry["link"])
            delay = 5 if i > 0 else 0
            send_success = False

            if images:
                # 有图：合并发送（图片+文字）
                send_success = await send_photo_with_caption(session, images[0], caption, delay)
            else:
                # 无图：纯文本发送
                send_success = await send_text(session, caption, delay)

            if send_success:
                success_links.append(entry["link"])

    if success_links:
        sent_links.extend(success_links)
        save_sent_posts(sent_links)
    else:
        logging.info("无成功推送的内容，不更新去重记录")

# ====================== 主函数（不变）======================
async def main():
    logging.info("===== SafeW RSS推送脚本启动 =====")
    
    config_check = True
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("❌ SAFEW_BOT_TOKEN格式无效")
        config_check = False
    if not SAFEW_CHAT_ID:
        logging.error("❌ 未配置SAFEW_CHAT_ID")
        config_check = False
    if not RSS_FEED_URL:
        logging.error("❌ 未配置RSS_FEED_URL")
        config_check = False
    if not config_check:
        logging.error("基础配置错误，脚本终止")
        return

    logging.info(f"当前aiohttp版本：{aiohttp.__version__}")
    if aiohttp.__version__ < "3.8.0":
        logging.warning("⚠️ aiohttp版本过低，可能有兼容问题")

    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"❌ 核心逻辑异常：{str(e)}")
    
    logging.info("===== SafeW RSS推送脚本结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
