import feedparser
import logging
import asyncio
import json
import os
import aiohttp
from bs4 import BeautifulSoup
from aiohttp import FormData

# ====================== 环境配置（无需修改，通过Secrets控制）======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")       # SafeW机器人令牌（格式：数字:字符）
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")           # 目标群组ID：10000294405
RSS_FEED_URL = os.getenv("RSS_FEED_URL")             # RSS源地址（新域名对应源）
POSTS_FILE = "sent_posts.json"                       # 去重记录文件（根目录）
MAX_PUSH_PER_RUN = 5                                 # 单次最多推送5条（避免刷屏）
FIXED_PROJECT_URL = "https://tyw29.cc/"              # 固定项目地址（新域名）
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"  # 最新浏览器UA

# ====================== 日志配置（输出关键调试信息）======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 去重记录管理（按link去重，可靠）======================
def load_sent_posts():
    """读取已推送的链接记录（根目录sent_posts.json）"""
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
    """保存已推送的链接记录到根目录"""
    try:
        with open(POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(post_links, f, ensure_ascii=False, indent=2)
        logging.info(f"已保存{len(post_links)}条去重记录（推送到群组：{SAFEW_CHAT_ID}）")
    except Exception as e:
        logging.error(f"保存去重记录失败：{str(e)}")

# ====================== RSS获取与去重（适配新域名）======================
def fetch_updates():
    """获取RSS源并按link去重，避免源本身重复"""
    try:
        logging.info(f"正在获取RSS源：{RSS_FEED_URL}")
        feed = feedparser.parse(RSS_FEED_URL)
        
        # 处理RSS解析错误
        if feed.bozo:
            logging.error(f"RSS解析失败：{feed.bozo_exception}")
            return None
        
        # 按link去重（核心去重逻辑）
        unique_entries = []
        seen_links = set()
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if link and link not in seen_links:
                seen_links.add(link)
                unique_entries.append(entry)
        
        logging.info(f"RSS源原始条目{len(feed.entries)}条，去重后剩余{len(unique_entries)}条有效内容")
        return unique_entries
    except Exception as e:
        logging.error(f"获取RSS源异常：{str(e)}")
        return None

# ====================== 网页图片提取（适配新域名tyw29.cc）======================
async def get_images_from_webpage(session, webpage_url):
    """从帖子页面提取目标div（class="message break-all" isfirst="1"）中的图片"""
    try:
        # 1. 模拟浏览器请求网页HTML
        headers = {"User-Agent": USER_AGENT, "Referer": FIXED_PROJECT_URL}  # 带Referer避免反爬
        async with session.get(webpage_url, headers=headers, timeout=15) as resp:
            if resp.status != 200:
                logging.warning(f"帖子页面请求失败（状态码：{resp.status}）：{webpage_url}")
                return []
            html = await resp.text()
        
        # 2. 解析HTML定位目标div
        soup = BeautifulSoup(html, "html.parser")
        target_div = soup.find("div", class_="message break-all", isfirst="1")
        if not target_div:
            logging.warning(f"未找到目标div（class=message break-all isfirst=1）：{webpage_url}")
            return []
        
        # 3. 提取图片链接（处理相对路径）
        img_tags = target_div.find_all("img", src=True)  # 仅保留有src的img标签
        if not img_tags:
            logging.info(f"目标div中无图片：{webpage_url}")
            return []
        
        # 处理相对路径（如"/uploads/123.jpg" → "https://tyw29.cc/uploads/123.jpg"）
        images = []
        base_domain = "/".join(webpage_url.split("/")[:3])  # 动态提取域名（适配新域名）
        for img in img_tags:
            img_src = img["src"].strip()
            if img_src.startswith("/"):
                img_src = f"{base_domain}{img_src}"
            # 验证图片URL格式（避免无效链接）
            if img_src.startswith(("http://", "https://")):
                images.append(img_src)
        
        logging.info(f"从帖子{webpage_url}提取到{len(images)}张有效图片")
        return images[:1]  # 仅取第一张图片（避免多图刷屏）
    except Exception as e:
        logging.error(f"提取图片异常（{webpage_url}）：{str(e)}")
        return []

# ====================== Markdown特殊字符转义（避免格式错误）======================
def escape_markdown(text):
    """转义Markdown特殊字符（_*~`>#+-.!()）"""
    special_chars = r"_*~`>#+-.!()"
    for char in special_chars:
        if char in text:
            text = text.replace(char, f"\{char}")
    return text

# ====================== 图片发送（强制multipart/form-data格式，解决400错误）======================
async def send_photo(session, image_url, delay=5):
    """发送图片到SafeW（按文档要求用multipart/form-data，手动控制Content-Type）"""
    try:
        await asyncio.sleep(delay)  # 间隔避免频率限制
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendPhoto"
        
        # 1. 构建FormData（仅保留必选参数，排除干扰）
        form = FormData(charset="utf-8")  # 明确编码，避免乱码
        form.add_field("chat_id", SAFEW_CHAT_ID)  # 必选：群组ID（确保为字符串）
        form.add_field("photo", image_url)        # 必选：图片URL（已验证有效性）
        
        # 2. 手动构造Content-Type（含boundary，解决自动设置失效问题）
        boundary = form._boundary.decode("utf-8")  # 提取FormData自动生成的分隔符
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        
        # 3. 发送POST请求（强制指定headers，延长超时）
        logging.info(f"正在发送图片：{image_url[:60]}...（API地址：{api_url[:60]}...）")
        async with session.post(
            api_url,
            data=form,
            headers=headers,
            timeout=20
        ) as response:
            response_text = await response.text() or "无响应内容"
            
            # 处理响应结果
            if response.status == 200:
                logging.info(f"✅ 图片发送成功：{image_url[:50]}...")
                return True
            else:
                logging.error(f"❌ 图片发送失败（状态码：{response.status}）\n排查指引：\n1. 图片URL测试：{image_url}（复制到浏览器打开）\n2. 群组ID验证：{SAFEW_CHAT_ID}（确认是目标群组ID）\n3. 响应详情：{response_text}")
                return False
    except Exception as e:
        logging.error(f"❌ 图片发送异常：{str(e)}")
        return False

# ====================== 文本发送（无图时使用，无占位符）======================
async def send_text(session, caption, delay=5):
    """发送纯文本消息（无图片时，格式：标题+发起者+链接+项目地址）"""
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendMessage"
        
        # 构造文本内容（无【图片】占位符）
        payload = {
            "chat_id": SAFEW_CHAT_ID,
            "text": caption,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,  # 禁用链接预览（避免刷屏）
            "disable_notification": False      # 启用消息通知
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
    """检查RSS新内容，按规则推送"""
    # 1. 读取已推送记录
    sent_links = load_sent_posts()
    # 2. 获取去重后的RSS内容
    rss_entries = fetch_updates()
    if not rss_entries:
        logging.info("无有效RSS内容，结束推送")
        return

    # 3. 筛选未推送的新内容
    new_entries = []
    for entry in rss_entries:
        link = entry.get("link", "").strip()
        title = entry.get("title", "无标题").strip()
        # 提取作者（优先author，无则用dc_author，最后默认"未知用户"）
        author = entry.get("author", entry.get("dc_author", "未知用户")).strip()
        
        if link and link not in sent_links:
            new_entries.append({"title": title, "author": author, "link": link})

    if not new_entries:
        logging.info("无新内容需要推送")
        return

    # 4. 限制单次推送数量（避免刷屏）
    push_entries = new_entries[:MAX_PUSH_PER_RUN]
    logging.info(f"发现{len(new_entries)}条新内容，本次推送前{len(push_entries)}条")

    # 5. 异步发送（有图发图，无图发文本）
    async with aiohttp.ClientSession() as session:
        success_links = []
        for i, entry in enumerate(push_entries):
            # 构造文本内容（无图片时使用）
            title = escape_markdown(entry["title"])
            author = escape_markdown(entry["author"])
            link = escape_markdown(entry["link"])
            caption = (
                f"{title}\n"
                f"由 @{author} 发起的话题讨论\n"
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"
            )
            
            # 提取图片（新域名适配）
            images = await get_images_from_webpage(session, entry["link"])
            # 发送控制（有图先发图，再补文本；无图直接发文本）
            delay = 5 if i > 0 else 0  # 第一条立即发，后续间隔5秒
            send_success = False

            if images:
                # 先发送图片
                img_success = await send_photo(session, images[0], delay)
                if img_success:
                    # 图片发送成功后，补充发送文本说明
                    text_success = await send_text(session, caption, delay=1)
                    send_success = text_success
            else:
                # 无图直接发送文本
                send_success = await send_text(session, caption, delay)

            # 记录成功推送的链接
            if send_success:
                success_links.append(entry["link"])

    # 6. 更新去重记录（仅保存成功推送的链接）
    if success_links:
        sent_links.extend(success_links)
        save_sent_posts(sent_links)
    else:
        logging.info("无成功推送的内容，不更新去重记录")

# ====================== 主函数（配置校验+版本兼容）======================
async def main():
    logging.info("===== SafeW RSS推送脚本启动 =====")
    
    # 1. 基础配置校验（避免低级错误）
    config_check = True
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("⚠️ 错误：SAFEW_BOT_TOKEN格式无效（应为 数字:字符 格式，如123456:ABCdef）")
        config_check = False
    if not SAFEW_CHAT_ID or SAFEW_CHAT_ID != "10000294405":
        logging.warning(f"⚠️ 警告：当前群组ID为{SAFEW_CHAT_ID}，请确认是否为目标群组ID 10000294405")
    if not RSS_FEED_URL:
        logging.error("⚠️ 错误：未配置RSS_FEED_URL（需在Secrets中设置）")
        config_check = False
    if not config_check:
        logging.error("基础配置错误，脚本终止运行")
        return

    # 2. 依赖版本校验（确保aiohttp兼容）
    logging.info(f"当前aiohttp版本：{aiohttp.__version__}（推荐≥3.8.0）")
    if aiohttp.__version__ < "3.8.0":
        logging.warning("⚠️ aiohttp版本过低，可能导致FormData异常，建议升级到3.8.0+")

    # 3. 执行推送逻辑
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"核心推送逻辑异常：{str(e)}")
    
    logging.info("===== SafeW RSS推送脚本结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
