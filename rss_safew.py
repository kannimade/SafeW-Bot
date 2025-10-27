import feedparser
import logging
import asyncio
import json
import os
import aiohttp
import uuid
import re  # 新增：用于提取TID
from bs4 import BeautifulSoup

# ====================== 环境配置 =======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")
RSS_FEED_URL = os.getenv("RSS_FEED_URL")
# 存储文件改为保存"最大TID"（替代原链接列表）
MAX_TID_FILE = "max_tid.json"  
MAX_PUSH_PER_RUN = 5
FIXED_PROJECT_URL = "https://tyw29.cc/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# ====================== 日志配置 =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 工具函数：提取TID（核心新增）=======================
def extract_tid_from_url(url):
    """从帖子URL中提取TID（如从"thread-16353.htm"提取16353）"""
    try:
        # 正则匹配：匹配"thread-"后、".htm"前的数字
        match = re.search(r'thread-(\d+)\.htm', url)
        if match:
            return int(match.group(1))  # 返回整数TID
        logging.warning(f"无法提取TID：{url}（URL格式异常）")
        return None
    except Exception as e:
        logging.error(f"提取TID失败（{url}）：{str(e)}")
        return None

# ====================== 去重逻辑修改：存储/读取最大TID =======================
def load_max_tid():
    """读取已推送的最大TID（替代原链接列表）"""
    try:
        if os.path.exists(MAX_TID_FILE):
            with open(MAX_TID_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    max_tid = int(content)
                    logging.info(f"读取到已推送最大TID：{max_tid}")
                    return max_tid
        # 首次运行/文件为空，默认最大TID为0（推送所有大于0的帖子）
        logging.info("首次运行/无历史TID记录，默认最大TID为0")
        return 0
    except (ValueError, json.JSONDecodeError):
        logging.error(f"{MAX_TID_FILE}格式错误，重置最大TID为0")
        with open(MAX_TID_FILE, "w", encoding="utf-8") as f:
            f.write("0")
        return 0
    except Exception as e:
        logging.error(f"读取最大TID失败：{str(e)}")
        return 0

def save_max_tid(new_max_tid):
    """保存新的最大TID（替代原链接列表保存）"""
    try:
        with open(MAX_TID_FILE, "w", encoding="utf-8") as f:
            f.write(str(new_max_tid))  # 直接保存数字字符串，无需JSON
        logging.info(f"已更新最大TID为：{new_max_tid}（后续仅推送大于该值的帖子）")
    except Exception as e:
        logging.error(f"保存最大TID失败：{str(e)}")

# ====================== RSS获取与筛选（基于TID过滤）======================
def fetch_updates():
    """获取RSS源，过滤出TID大于当前最大TID的新帖子"""
    try:
        logging.info(f"正在获取RSS源：{RSS_FEED_URL}")
        feed = feedparser.parse(RSS_FEED_URL)
        
        if feed.bozo:
            logging.error(f"RSS解析失败：{feed.bozo_exception}")
            return None
        
        # 1. 读取当前最大TID
        current_max_tid = load_max_tid()
        # 2. 过滤+提取有效帖子（含TID）
        valid_entries = []
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if not link:
                continue
            # 提取TID
            tid = extract_tid_from_url(link)
            if not tid:
                continue
            # 仅保留TID > 当前最大TID的帖子（避免推送历史数据）
            if tid > current_max_tid:
                # 给条目添加tid字段，方便后续排序
                entry["tid"] = tid
                valid_entries.append(entry)
        
        logging.info(f"RSS源原始条目{len(feed.entries)}条，过滤后剩余{len(valid_entries)}条新帖子（TID > {current_max_tid}）")
        return valid_entries
    except Exception as e:
        logging.error(f"获取RSS源异常：{str(e)}")
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

# ====================== Markdown特殊字符转义（不变）======================
def escape_markdown(text):
    special_chars = r"_*~`>#+!()"
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

# ====================== 核心推送逻辑（新增排序+TID更新）======================
async def check_for_updates():
    # 1. 获取过滤后的新帖子（TID > 当前最大TID）
    rss_entries = fetch_updates()
    if not rss_entries:
        logging.info("无有效新帖子，结束推送")
        return

    # 2. 按TID升序排序（从小到大发送，实现顺序浏览）
    # 排序依据：每个entry的"tid"字段（fetch_updates中已添加）
    rss_entries_sorted = sorted(rss_entries, key=lambda x: x["tid"])
    logging.info(f"新帖子按TID升序排序：{[entry['tid'] for entry in rss_entries_sorted]}")

    # 3. 限制单次推送数量（避免刷屏）
    push_entries = rss_entries_sorted[:MAX_PUSH_PER_RUN]
    logging.info(f"本次推送前{len(push_entries)}条帖子（TID顺序：{[entry['tid'] for entry in push_entries]}）")

    # 4. 异步发送内容
    async with aiohttp.ClientSession() as session:
        # 记录本次推送的最大TID（用于后续更新）
        pushed_tids = []
        for i, entry in enumerate(push_entries):
            link = entry.get("link", "").strip()
            title = entry.get("title", "无标题").strip()
            author = entry.get("author", entry.get("dc_author", "未知用户")).strip()
            tid = entry["tid"]  # fetch_updates中已提取的TID

            # 构造文字内容
            title_escaped = escape_markdown(title)
            author_escaped = escape_markdown(author)
            caption = (
                f"{title_escaped}\n"
                f"由 @{author_escaped} 发起的话题讨论\n"
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"
            )
            
            # 提取图片
            images = await get_images_from_webpage(session, link)
            delay = 5 if i > 0 else 0  # 第一条立即发，后续间隔5秒
            send_success = False

            if images:
                # 有图：合并发送（图片+文字）
                send_success = await send_photo_with_caption(session, images[0], caption, delay)
            else:
                # 无图：纯文本发送
                send_success = await send_text(session, caption, delay)

            # 发送成功则记录TID
            if send_success:
                pushed_tids.append(tid)
                logging.info(f"✅ 已推送TID：{tid}（{link}）")

    # 5. 更新最大TID（仅当有成功推送的帖子时）
    if pushed_tids:
        # 取本次推送的最大TID作为新的全局最大TID
        new_max_tid = max(pushed_tids)
        save_max_tid(new_max_tid)
    else:
        logging.info("无成功推送的帖子，不更新最大TID")

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
