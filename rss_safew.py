import feedparser
import logging
import asyncio
import json
import os
import aiohttp
import uuid  # 用于生成multipart分隔符
from bs4 import BeautifulSoup

# ====================== 环境配置（通过环境变量/Secrets传入）======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")       # 机器人令牌（格式：数字:字符）
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")           # 目标群组ID（如：10000294405）
RSS_FEED_URL = os.getenv("RSS_FEED_URL")             # RSS源地址
POSTS_FILE = "sent_posts.json"                       # 去重记录文件（本地存储）
MAX_PUSH_PER_RUN = 5                                 # 单次最多推送5条（防刷屏）
FIXED_PROJECT_URL = "https://tyw29.cc/"              # 项目固定域名
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"  # 浏览器模拟UA

# ====================== 日志配置（输出详细步骤和错误）======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 去重记录管理（避免重复推送）======================
def load_sent_posts():
    """读取已推送的帖子链接接记录"""
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
    """保存已推送的帖子链接记录"""
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_links, f, ensure_ascii=False, indent=2)
        logging.info(f"✅ 已保存{len(post_links)}条去重记录（群组：{SAFEW_CHAT_ID}）")
    except Exception as e:
        logging.error(f"❌ 保存去重记录失败：{str(e)}")

# ====================== RSS源获取与去重======================
def fetch_updates():
    """获取RSS源并按链接去重"""
    try:
        logging.info(f"正在获取RSS源：{RSS_FEED_URL}")
        feed = feedparser.parse(RSS_FEED_URL)
        
        # 处理RSS解析错误
        if feed.bozo:
            logging.error(f"❌ RSS解析失败：{feed.bozo_exception}")
            return None
        
        # 按链接去重（核心去重逻辑）
        unique_entries = []
        seen_links = set()
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if link and link not in seen_links:
                seen_links.add(link)
                unique_entries.append(entry)
        
        logging.info(f"✅ RSS源原始条目{len(feed.entries)}条，去重后剩余{len(unique_entries)}条")
        return unique_entries
    except Exception as e:
        logging.error(f"❌ 获取RSS源异常：{str(e)}")
        return None

# ====================== 网页图片提取（适配tyw29.cc）======================
async def get_images_from_webpage(session, webpage_url):
    """从帖子页面提取图片（处理相对路径和懒加载）"""
    try:
        # 构造请求头（模拟浏览器，避免反爬）
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": FIXED_PROJECT_URL,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2"
        }
        
        # 请求帖子页面HTML
        async with session.get(webpage_url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                logging.warning(f"⚠️ 帖子请求失败（状态码：{resp.status}）：{webpage_url}")
                return []
            html = await resp.text()
        
        # 解析HTML，定位图片所在的div（根据日志确认的结构）
        soup = BeautifulSoup(html, "html.parser")
        target_divs = soup.find_all("div", class_="message break-all", isfirst="1")
        logging.info(f"找到目标内容div数量：{len(target_divs)}")
        if not target_divs:
            return []
        
        # 提取图片链接（处理相对路径和懒加载）
        images = []
        base_domain = "/".join(webpage_url.split("/")[:3])  # 动态获取域名（如https://tyw29.cc）
        for div in target_divs:
            img_tags = div.find_all("img")
            logging.info(f"目标div中找到{len(img_tags)}个img标签")
            
            for img in img_tags:
                # 优先取懒加载地址（data-src），再取src
                img_url = img.get("data-src", "").strip() or img.get("src", "").strip()
                # 过滤无效链接（空值、base64图片、JS链接）
                if not img_url or img_url.startswith(("data:image/", "javascript:")):
                    continue
                
                # 处理相对路径（两种情况：带/和不带/）
                if img_url.startswith("/"):
                    img_url = f"{base_domain}{img_url}"  # 如/upload/xxx → https://xxx/upload/xxx
                elif not img_url.startswith(("http://", "https://")):
                    img_url = f"{base_domain}/{img_url}"  # 如upload/xxx → https://xxx/upload/xxx
                
                # 验证有效URL并去重
                if img_url.startswith(("http://", "https://")) and img_url not in images:
                    images.append(img_url)
                    logging.info(f"✅ 提取到图片URL：{img_url[:60]}...")
        
        if images:
            logging.info(f"从{webpage_url}成功提取{len(images)}张图片")
            return images[:1]  # 仅取第一张（避免刷屏）
        else:
            logging.warning(f"⚠️ 找到img标签但未提取到有效图片：{webpage_url}")
            return []
    except Exception as e:
        logging.error(f"❌ 提取图片异常：{str(e)}")
        return []

# ====================== Markdown特殊字符转义（避免格式错误）======================
def escape_markdown(text):
    """转义Markdown特殊字符（_*~`>#+-.!()）"""
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        if char in text:
            text = text.replace(char, f"\{char}")
    return text

# ====================== 图片发送（先下载再上传文件，解决“缺少file”错误）======================
async def send_photo(session, image_url, delay=5):
    """发送图片到SafeW（下载图片二进制数据后以文件形式上传）"""
    try:
        # 发送前延迟（避免频率限制）
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendPhoto"
        logging.info(f"\n=== 开始处理图片 ===")
        logging.info(f"图片URL：{image_url[:60]}...")
        logging.info(f"API地址：{api_url[:60]}...")

        # 1. 下载图片二进制数据（核心：获取实际文件内容）
        try:
            img_headers = {
                "User-Agent": USER_AGENT,
                "Referer": FIXED_PROJECT_URL
            }
            async with session.get(
                image_url,
                headers=img_headers,
                timeout=15,
                ssl=False  # 临时关闭SSL（生产环境建议开启）
            ) as img_resp:
                if img_resp.status != 200:
                    logging.error(f"❌ 图片下载失败（状态码：{img_resp.status}）")
                    return False
                img_data = await img_resp.read()  # 二进制图片数据
                img_content_type = img_resp.headers.get("Content-Type", "image/jpeg")
                img_size = len(img_data)
                logging.info(f"✅ 图片下载完成：大小{img_size}字节，类型{img_content_type}")

                # 校验图片大小（限制10MB内）
                if img_size > 10 * 1024 * 1024:
                    logging.error(f"❌ 图片超过10MB限制（当前{img_size/1024/1024:.2f}MB）")
                    return False
        except asyncio.TimeoutError:
            logging.error(f"❌ 图片下载超时（15秒）")
            return False
        except Exception as e:
            logging.error(f"❌ 图片下载异常：{str(e)}")
            return False

        # 2. 生成multipart分隔符（boundary）
        try:
            boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
            logging.info(f"生成boundary：{boundary}")
        except Exception as e:
            logging.error(f"❌ 生成boundary失败：{str(e)}")
            return False

        # 3. 构造multipart/form-data请求体
        try:
            chat_id_str = str(SAFEW_CHAT_ID)
            # 提取并清理文件名（去除特殊字符）
            filename = image_url.split("/")[-1].split("?")[0].replace('"', '').replace("'", "").replace(" ", "_")
            logging.info(f"处理后的文件名：{filename}")

            # 拼接请求体（文本部分+二进制文件部分）
            text_part = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="chat_id"\r\n'
                f"\r\n"
                f"{chat_id_str}\r\n"
            ).encode("utf-8")

            file_part_header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
                f"Content-Type: {img_content_type}\r\n"
                f"\r\n"
            ).encode("utf-8")

            end_part = f"\r\n--{boundary}--\r\n".encode("utf-8")
            body = text_part + file_part_header + img_data + end_part
            logging.info(f"✅ 请求体构造完成（总大小：{len(body)}字节）")
        except Exception as e:
            logging.error(f"❌ 构造请求体失败：{str(e)}")
            return False

        # 4. 发送请求（上传文件）
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
                response_summary = response_text[:150] + "..." if len(response_text) > 150 else response_text
                logging.info(f"收到响应：状态码{response.status}，内容：{response_summary}")

                if response.status == 200:
                    logging.info(f"✅ 图片发送成功！")
                    return True
                else:
                    logging.error(f"❌ 图片发送失败（状态码：{response.status}），响应：{response_text}")
                    return False
        except asyncio.TimeoutError:
            logging.error(f"❌ 发送请求超时（20秒）")
            return False
        except Exception as e:
            logging.error(f"❌ 发送请求异常：{str(e)}")
            return False

    except Exception as e:
        logging.error(f"❌ 图片发送总异常：{str(e)}")
        return False

# ====================== 文本发送（无图时使用）======================
async def send_text(session, caption, delay=5):
    """发送纯文本消息（标题+作者+链接）"""
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,  # 禁用链接预览
            "disable_notification": False      # 启用通知
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

# ====================== 核心推送逻辑（有图发图，无图发文本）======================
async def check_for_updates():
    """检查RSS新内容并推送"""
    # 1. 读取已推送记录
    sent_links = load_sent_posts()
    # 2. 获取RSS内容
    rss_entries = fetch_updates()
    if not rss_entries:
        logging.info("无有效RSS内容，结束推送")
        return

    # 3. 筛选未推送的新内容
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

    # 4. 限制单次推送数量
    push_entries = new_entries[:MAX_PUSH_PER_RUN]
    logging.info(f"发现{len(new_entries)}条新内容，本次推送前{len(push_entries)}条")

    # 5. 异步发送内容
    async with aiohttp.ClientSession() as session:
        success_links = []
        for i, entry in enumerate(push_entries):
            # 构造文本内容（Markdown转义）
            title = escape_markdown(entry["title"])
            author = escape_markdown(entry["author"])
            link = escape_markdown(entry["link"])
            caption = (
                f"{title}\n"
                f"由 @{author} 发起的话题讨论\n"
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"
            )
            
            # 提取图片
            images = await get_images_from_webpage(session, entry["link"])
            delay = 5 if i > 0 else 0  # 第一条立即发，后续间隔5秒
            send_success = False

            if images:
                # 有图：先发图，再发文本
                img_success = await send_photo(session, images[0], delay)
                if img_success:
                    text_success = await send_text(session, caption, delay=1)
                    send_success = text_success
            else:
                # 无图：直接发文本
                send_success = await send_text(session, caption, delay)

            # 记录成功推送的链接
            if send_success:
                success_links.append(entry["link"])

    # 6. 更新去重记录
    if success_links:
        sent_links.extend(success_links)
        save_sent_posts(sent_links)
    else:
        logging.info("无成功推送的内容，不更新去重记录")

# ====================== 主函数（入口逻辑）======================
async def main():
    logging.info("===== SafeW RSS推送脚本启动 =====")
    
    # 基础配置校验
    config_check = True
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("❌ 错误：SAFEW_BOT_TOKEN格式无效（应为 数字:字符）")
        config_check = False
    if not SAFEW_CHAT_ID:
        logging.error("❌ 错误：未配置SAFEW_CHAT_ID（目标群组ID）")
        config_check = False
    if not RSS_FEED_URL:
        logging.error("❌ 错误：未配置RSS_FEED_URL（RSS源地址）")
        config_check = False
    if not config_check:
        logging.error("基础配置错误，脚本终止")
        return

    # 依赖版本校验
    logging.info(f"当前aiohttp版本：{aiohttp.__version__}（推荐≥3.8.0）")
    if aiohttp.__version__ < "3.8.0":
        logging.warning("⚠️ aiohttp版本过低，可能存在兼容问题")

    # 执行推送逻辑
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"❌ 核心推送逻辑异常：{str(e)}")
    
    logging.info("===== SafeW RSS推送脚本结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
