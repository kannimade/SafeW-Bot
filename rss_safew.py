import feedparser
import logging
import asyncio
import json
import os
import aiohttp
import uuid
import re  # 用于提取TID
from bs4 import BeautifulSoup

# ====================== 环境配置 =======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")
RSS_FEED_URL = os.getenv("RSS_FEED_URL")
MAX_TID_FILE = "max_tid.json"  # 存储最大TID的文件
MAX_PUSH_PER_RUN = 5
FIXED_PROJECT_URL = "https://tyw29.cc/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# ====================== 日志配置 =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 1. TID提取（强化正则，确保准确）=======================
def extract_tid_from_url(url):
    """从URL提取TID（强化正则，适配可能的URL格式）"""
    try:
        # 正则匹配：thread-数字.htm（允许数字前后有非数字字符，仅取连续数字）
        match = re.search(r'thread-(\d+)\.htm', url)
        if match:
            tid = int(match.group(1))
            logging.debug(f"从URL提取TID成功：{url} → {tid}")  # 调试日志
            return tid
        logging.warning(f"无法提取TID：{url}（URL格式不符合thread-数字.htm）")
        return None
    except Exception as e:
        logging.error(f"提取TID失败（{url}）：{str(e)}")
        return None

# ====================== 2. TID存储/读取（修复文件读写逻辑）======================
def load_max_tid():
    """读取最大TID（确保文件存在且内容正确）"""
    try:
        # 检查文件是否存在，不存在则创建并返回0
        if not os.path.exists(MAX_TID_FILE):
            logging.info(f"{MAX_TID_FILE}不存在，创建并初始化最大TID为0")
            with open(MAX_TID_FILE, "w", encoding="utf-8") as f:
                f.write("0")
            return 0
        
        # 读取文件内容
        with open(MAX_TID_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
        
        # 验证内容是否为数字
        if not content.isdigit():
            logging.error(f"{MAX_TID_FILE}内容无效（非数字）：{content}，重置为0")
            with open(MAX_TID_FILE, "w", encoding="utf-8") as f:
                f.write("0")
            return 0
        
        max_tid = int(content)
        logging.info(f"成功读取最大TID：{max_tid}（文件：{MAX_TID_FILE}）")
        return max_tid
    except Exception as e:
        logging.error(f"读取最大TID失败：{str(e)}，强制返回0")
        return 0

def save_max_tid(new_max_tid):
    """保存最大TID（确保写入成功）"""
    try:
        # 验证新TID是否为有效数字
        if not isinstance(new_max_tid, int) or new_max_tid < 0:
            logging.error(f"无效的新TID：{new_max_tid}，拒绝保存")
            return
        
        # 写入文件（覆盖原有内容）
        with open(MAX_TID_FILE, "w", encoding="utf-8") as f:
            f.write(str(new_max_tid))
        
        # 验证写入结果
        with open(MAX_TID_FILE, "r", encoding="utf-8") as f:
            saved = f.read().strip()
        if saved == str(new_max_tid):
            logging.info(f"成功保存最大TID：{new_max_tid}（文件：{MAX_TID_FILE}）")
        else:
            logging.error(f"保存TID失败：预期{new_max_tid}，实际保存{saved}")
    except Exception as e:
        logging.error(f"保存最大TID异常：{str(e)}")

# ====================== 3. RSS获取与筛选（严格过滤旧帖）======================
def fetch_updates():
    """获取RSS并筛选TID > 当前最大TID的新帖"""
    try:
        current_max_tid = load_max_tid()
        logging.info(f"开始筛选新帖（TID > {current_max_tid}）")
        
        feed = feedparser.parse(RSS_FEED_URL)
        if feed.bozo:
            logging.error(f"RSS解析失败：{feed.bozo_exception}")
            return None
        
        valid_entries = []
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if not link:
                logging.debug("跳过无链接的条目")
                continue
            
            tid = extract_tid_from_url(link)
            if not tid:
                continue  # 跳过无法提取TID的条目
            
            # 严格筛选：仅保留TID > 当前最大TID的条目
            if tid > current_max_tid:
                entry["tid"] = tid
                valid_entries.append(entry)
                logging.debug(f"保留新帖：TID={tid}，链接={link}")
            else:
                logging.debug(f"跳过旧帖：TID={tid}（≤ 当前最大{current_max_tid}）")
        
        logging.info(f"筛选完成：共{len(valid_entries)}条新帖（TID > {current_max_tid}）")
        return valid_entries
    except Exception as e:
        logging.error(f"获取RSS异常：{str(e)}")
        return None

# ====================== 4. 图片提取（不变）======================
async def get_images_from_webpage(session, webpage_url):
    try:
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": FIXED_PROJECT_URL
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
                    logging.info(f"提取到图片：{img_url[:60]}...")
        
        return images[:1] if images else []
    except Exception as e:
        logging.error(f"提取图片异常：{str(e)}")
        return []

# ====================== 5. Markdown转义（移除@转义，避免反斜杠）======================
def escape_markdown(text):
    """仅转义影响格式的字符，不转义@（避免反斜杠）"""
    special_chars = r"_*~`>#+!()"  # 移除@，避免转义后显示\@
    for char in special_chars:
        if char in text:
            text = text.replace(char, f"\{char}")
    return text

# ====================== 6. 图片+文字发送（不变）=======================
async def send_photo_with_caption(session, image_url, caption, delay=5):
    try:
        await asyncio.sleep(delay)
        api_url = f"https://api.safew.org/bot{SAFEW_BOT_TOKEN}/sendPhoto"
        logging.info(f"处理带文字的图片：{image_url[:60]}...")

        # 下载图片
        img_headers = {"User-Agent": USER_AGENT, "Referer": FIXED_PROJECT_URL}
        async with session.get(image_url, headers=img_headers, timeout=15, ssl=False) as img_resp:
            if img_resp.status != 200:
                logging.error(f"图片下载失败（{img_resp.status}）")
                return False
            img_data = await img_resp.read()
            img_content_type = img_resp.headers.get("Content-Type", "image/jpeg")

        # 构造请求体
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
        chat_id_str = str(SAFEW_CHAT_ID)
        filename = image_url.split("/")[-1].split("?")[0].replace('"', '').replace("'", "").replace(" ", "_")

        text_parts = [
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id_str}\r\n',
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'
        ]
        text_part = "".join(text_parts).encode("utf-8")
        file_part_header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
            f"Content-Type: {img_content_type}\r\n\r\n"
        ).encode("utf-8")
        end_part = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = text_part + file_part_header + img_data + end_part

        # 发送请求
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": USER_AGENT,
            "Content-Length": str(len(body))
        }
        async with session.post(api_url, data=body, headers=headers, timeout=20, ssl=False) as response:
            response_text = await response.text()
            if response.status == 200:
                logging.info("图片+文字发送成功")
                return True
            logging.error(f"发送失败（{response.status}）：{response_text}")
            return False
    except Exception as e:
        logging.error(f"图片发送异常：{str(e)}")
        return False

# ====================== 7. 纯文本发送（不变）=======================
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
                logging.info("纯文本发送成功")
                return True
            response_text = await response.text()
            logging.error(f"文本发送失败（{response.status}）：{response_text}")
            return False
    except Exception as e:
        logging.error(f"文本发送异常：{str(e)}")
        return False

# ====================== 8. 核心推送逻辑（修复排序+存储）======================
async def check_for_updates():
    # 获取新帖
    rss_entries = fetch_updates()
    if not rss_entries:
        logging.info("无新帖，结束推送")
        return

    # 按TID升序排序（从小到大推送）
    rss_entries_sorted = sorted(rss_entries, key=lambda x: x["tid"])
    logging.info(f"新帖按TID升序：{[e['tid'] for e in rss_entries_sorted]}")

    # 限制单次推送数量
    push_entries = rss_entries_sorted[:MAX_PUSH_PER_RUN]
    logging.info(f"本次推送{len(push_entries)}条：{[e['tid'] for e in push_entries]}")

    # 发送并记录成功的TID
    async with aiohttp.ClientSession() as session:
        pushed_tids = []
        for i, entry in enumerate(push_entries):
            link = entry["link"]
            tid = entry["tid"]
            title = entry.get("title", "无标题").strip()
            author = entry.get("author", "未知用户").strip()

            # 构造文本（用全角＠替代半角@，避免跳转）
            title_escaped = escape_markdown(title)
            author_escaped = escape_markdown(author)
            caption = (
                f"{title_escaped}\n"
                f"由 ＠{author_escaped} 发起的话题讨论\n"  # 全角＠，无跳转且无反斜杠
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"
            )

            # 发送
            images = await get_images_from_webpage(session, link)
            delay = 5 if i > 0 else 0
            send_success = False

            if images:
                send_success = await send_photo_with_caption(session, images[0], caption, delay)
            else:
                send_success = await send_text(session, caption, delay)

            if send_success:
                pushed_tids.append(tid)
                logging.info(f"已推送TID：{tid}")

    # 更新最大TID（仅用成功推送的最大TID）
    if pushed_tids:
        new_max_tid = max(pushed_tids)
        save_max_tid(new_max_tid)
    else:
        logging.info("无成功推送，不更新TID")

# ====================== 主函数 =======================
async def main():
    logging.info("===== 推送脚本启动 =====")
    
    # 配置校验
    config_check = True
    if not SAFEW_BOT_TOKEN or ":" not in SAFEW_BOT_TOKEN:
        logging.error("SAFEW_BOT_TOKEN无效")
        config_check = False
    if not SAFEW_CHAT_ID:
        logging.error("未配置SAFEW_CHAT_ID")
        config_check = False
    if not RSS_FEED_URL:
        logging.error("未配置RSS_FEED_URL")
        config_check = False
    if not config_check:
        logging.error("配置错误，终止")
        return

    # 执行推送
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"核心逻辑异常：{str(e)}")
    
    logging.info("===== 推送脚本结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
