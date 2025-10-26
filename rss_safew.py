import feedparser
import logging
import asyncio
import json
import os
import aiohttp
import uuid  # 新增：用于生成boundary
from bs4 import BeautifulSoup
from aiohttp import FormData

# ====================== 环境配置 =======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")       # SafeW机器人令牌（格式：数字:字符）
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")           # 目标群组ID：10000294405
RSS_FEED_URL = os.getenv("RSS_FEED_URL")             # RSS源地址（新域名对应源）
POSTS_FILE = "sent_posts.json"                       # 去重记录文件（根目录）
MAX_PUSH_PER_RUN = 5                                 # 单次最多推送5条（避免刷屏）
FIXED_PROJECT_URL = "https://tyw29.cc/"              # 固定项目地址（新域名）
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"  # 浏览器UA

# ====================== 日志配置 =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 去重记录管理 =======================
def load_sent_posts():
    """读取已推送的链接记录"""
    try:
        if os.path.exists(POSTS_FILE):
            with open(POSTS_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return json.loads(content) if content else []
        logging.info("首次运行，初始化空去重记录")
        return []
    except json.JSONDecodeError:
        logging.error("sent_posts.json格式错误，重置为空列表")
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    except Exception as e:
        logging.error(f"读取去重记录失败：{str(e)}")
        return []

def save_sent_posts(post_links):
    """保存已推送的链接记录"""
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_links, f, ensure_ascii=False, indent=2)
        logging.info(f"已保存{len(post_links)}条去重记录")
    except Exception as e:
        logging.error(f"保存去重记录失败：{str(e)}")

# ====================== RSS获取与去重 =======================
def fetch_updates():
    """获取RSS源并去重"""
    try:
        logging.info(f"正在获取RSS源：{RSS_FEED_URL}")
        feed = feedparser.parse(RSS_FEED_URL)
        
        if feed.bozo:
            logging.error(f"RSS解析失败：{feed.bozo_exception}")
            return None
        
        unique_entries = []
        seen_links = set()
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if link and link not in seen_links:
                seen_links.add(link)
                unique_entries.append(entry)
        
        logging.info(f"RSS源原始条目{len(feed.entries)}条，去重后剩余{len(unique_entries)}条")
        return unique_entries
    except Exception as e:
        logging.error(f"获取RSS源异常：{str(e)}")
        return None

# ====================== 网页图片提取 =======================
async def get_images_from_webpage(session, webpage_url):
    """从帖子提取图片（处理相对路径和懒加载）"""
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": webpage_url,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2"
        }
        
        async with session.get(webpage_url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                logging.warning(f"帖子请求失败（{resp.status}）：{webpage_url}")
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
            return images[:1]  # 仅取第一张
        else:
            logging.warning(f"找到img标签但未提取到有效图片：{webpage_url}")
            return []
    except Exception as e:
        logging.error(f"提取图片异常：{str(e)}")
        return []

# ====================== Markdown特殊字符转义 =======================
def escape_markdown(text):
    """转义Markdown特殊字符"""
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        if char in text:
            text = text.replace(char, f"\{char}")
    return text

# ====================== 图片发送（完整实现，手动构造multipart）=======================
async def send_photo(session, image_url, delay=5):
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendPhoto"
        logging.info(f"开始发送图片：{image_url[:50]}，API地址：{api_url[:50]}")

        # 1. 生成boundary
        try:
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
            logging.info(f"生成boundary：{boundary}")
        except Exception as e:
            logging.error(f"❌ 生成boundary失败：{str(e)}")
            return False

        # 2. 构造请求体字段（强制字符串类型）
        try:
            chat_id_str = str(SAFEW_CHAT_ID)  # 确保是字符串
            image_url_str = str(image_url)     # 确保是字符串
            
            body_parts = [
                f"--{boundary}",
                'Content-Disposition: form-data; name="chat_id"',
                '',  # 空行分隔头和内容
                chat_id_str,
                f"--{boundary}",
                'Content-Disposition: form-data; name="photo"',
                '',  # 空行分隔头和内容
                image_url_str,
                f"--{boundary}--"  # 结束符
            ]
            logging.info(f"构造请求体字段完成（共{len(body_parts)}部分）")
        except Exception as e:
            logging.error(f"❌ 构造请求体字段失败：{str(e)}")
            return False

        # 3. 拼接并编码请求体（处理特殊字符）
        try:
            body = "\r\n".join(body_parts).encode("utf-8", errors="replace")
            logging.info(f"请求体编码完成（长度：{len(body)}字节）")
        except UnicodeEncodeError as e:
            logging.error(f"❌ 请求体编码失败（特殊字符）：{str(e)}")
            return False
        except Exception as e:
            logging.error(f"❌ 请求体拼接失败：{str(e)}")
            return False

        # 4. 构造请求头
        try:
            headers = {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": USER_AGENT,
                "Content-Length": str(len(body))  # 显式指定长度
            }
            logging.info(f"请求头：{headers}")
        except Exception as e:
            logging.error(f"❌ 构造请求头失败：{str(e)}")
            return False

        # 5. 发送请求
        try:
            async with session.post(
                api_url,
                data=body,
                headers=headers,
                timeout=20
            ) as response:
                response_text = await response.text() or "无响应内容"
                logging.info(f"响应状态码：{response.status}，响应内容：{response_text[:100]}")
                if response.status == 200:
                    logging.info(f"✅ 图片发送成功：{image_url[:50]}...")
                    return True
                else:
                    logging.error(f"❌ 图片发送失败（状态码：{response.status}），响应：{response_text}")
                    return False
        except Exception as e:
            logging.error(f"❌ 发送请求时异常：{str(e)}")
            return False

    except Exception as e:
        logging.error(f"❌ 图片发送总异常：{str(e)}")
        return False

# ====================== 文本发送（完整实现）=======================
async def send_text(session, caption, delay=5):
    """发送纯文本消息"""
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
            "disable_notification": False
        }
        
        async with session.post(api_url, json=payload, timeout=15) as response:
            if response.status == 200:
                logging.info("✅ 文本消息发送成功")
                return True
            else:
                response_text = await response.text() or "无响应内容"
                logging.error(f"❌ 文本发送失败（状态码：{response.status}），响应：{response_text}")
                return False
    except Exception as e:
        logging.error(f"❌ 文本发送异常：{str(e)}")
        return False

# ====================== 核心推送逻辑 =======================
async def check_for_updates():
    """检查RSS新内容并推送"""
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
    logging.info(f"发现{len(new_entries)}条新内容，本次推送前{len(push_entries)}条")

    async with aiohttp.ClientSession() as session:
        success_links = []
        for i, entry in enumerate(push_entries):
            title = escape_markdown(entry["title"])
            author = escape_markdown(entry["author"])
            link = escape_markdown(entry["link"])
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
                img_success = await send_photo(session, images[0], delay)
                if img_success:
                    text_success = await send_text(session, caption, delay=1)
                    send_success = text_success
            else:
                send_success = await send_text(session, caption, delay)

            if send_success:
                success_links.append(entry["link"])

    if success_links:
        sent_links.extend(success_links)
        save_sent_posts(sent_links)
    else:
        logging.info("无成功推送的内容，不更新去重记录")

# ====================== 主函数 =======================
async def main():
    logging.info("===== SafeW RSS推送脚本启动 =====")
    
    # 配置校验
    config_check = True
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("⚠️ 错误：SAFEW_BOT_TOKEN格式无效（应为 数字:字符）")
        config_check = False
    if not SAFEW_CHAT_ID or SAFEW_CHAT_ID != "10000294405":
        logging.warning(f"⚠️ 警告：当前群组ID为{SAFEW_CHAT_ID}，目标应为10000294405")
    if not RSS_FEED_URL:
        logging.error("⚠️ 错误：未配置RSS_FEED_URL")
        config_check = False
    if not config_check:
        logging.error("基础配置错误，脚本终止")
        return

    # 依赖版本校验
    logging.info(f"当前aiohttp版本：{aiohttp.__version__}（推荐≥3.8.0）")
    if aiohttp.__version__ < "3.8.0":
        logging.warning("⚠️ aiohttp版本过低，可能存在兼容问题")

    # 执行推送
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"核心推送逻辑异常：{str(e)}")
    
    logging.info("===== SafeW RSS推送脚本结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
