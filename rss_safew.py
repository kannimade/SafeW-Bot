import feedparser
import logging
import asyncio
import json
import os
import aiohttp
import uuid
import re
from bs4 import BeautifulSoup

# ====================== 环境配置 =======================
SAFEW_BOT_TOKEN = os.getenv("SAFEW_BOT_TOKEN")
SAFEW_CHAT_ID = os.getenv("SAFEW_CHAT_ID")
RSS_FEED_URL = os.getenv("RSS_FEED_URL")
PUSHED_TIDS_FILE = "sent_posts.json"  # 存储所有已推送TID的列表
MAX_PUSH_PER_RUN = 5
FIXED_PROJECT_URL = "https://tyw29.cc/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"

# ====================== 日志配置 =======================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ====================== 1. TID提取（不变）=======================
def extract_tid_from_url(url):
    try:
        match = re.search(r'thread-(\d+)\.htm', url)
        if match:
            tid = int(match.group(1))
            logging.debug(f"提取TID：{url} → {tid}")
            return tid
        logging.warning(f"无法提取TID：{url}")
        return None
    except Exception as e:
        logging.error(f"提取TID失败：{str(e)}")
        return None

# ====================== 2. 已推送TID的存储/读取（核心修改）======================
def load_sent_tids():
    """读取所有已推送的TID列表（存储在sent_posts.json）"""
    try:
        if not os.path.exists(SENT_POSTS_FILE):
            logging.info(f"{SENT_POSTS_FILE}不存在，初始化空列表")
            with open(SENT_POSTS_FILE, "w", encoding="utf-8") as f:
                json.dump([], f)
            return []
        
        with open(SENT_POSTS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return []
            tids = json.loads(content)
            if not isinstance(tids, list) or not all(isinstance(t, int) for t in tids):
                logging.error(f"{SENT_POSTS_FILE}格式错误，重置为空列表")
                with open(SENT_POSTS_FILE, "w", encoding="utf-8") as f:
                    json.dump([], f)
                return []
            logging.info(f"读取到已推送TID列表（共{len(tids)}条）：{tids[:5]}...")
            return tids
    except json.JSONDecodeError:
        logging.error(f"{SENT_POSTS_FILE}解析失败，重置为空列表")
        with open(SENT_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        return []
    except Exception as e:
        logging.error(f"读取已推送TID失败：{str(e)}，返回空列表")
        return []

def save_sent_tids(new_tids, existing_tids):
    """将新推送的TID添加到sent_posts.json（去重后保存）"""
    try:
        all_tids = list(set(existing_tids + new_tids))  # 去重
        all_tids_sorted = sorted(all_tids)  # 排序
        with open(SENT_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(all_tids_sorted, f, ensure_ascii=False, indent=2)
        logging.info(f"已更新{SENT_POSTS_FILE}：新增{len(new_tids)}条，总记录{len(all_tids_sorted)}条")
    except Exception as e:
        logging.error(f"保存{SENT_POSTS_FILE}失败：{str(e)}")

# ====================== 3. RSS获取与筛选（基于sent_posts.json筛选）======================
def fetch_updates():
    """获取RSS并筛选出不在sent_posts.json中的新帖"""
    try:
        sent_tids = load_sent_tids()
        logging.info(f"开始筛选新帖（排除已推送的{len(sent_tids)}个TID）")
        
        feed = feedparser.parse(RSS_FEED_URL)
        if feed.bozo:
            logging.error(f"RSS解析失败：{feed.bozo_exception}")
            return None
        
        valid_entries = []
        for entry in feed.entries:
            link = entry.get("link", "").strip()
            if not link:
                continue
            
            tid = extract_tid_from_url(link)
            if not tid:
                continue
            
            if tid not in sent_tids:
                entry["tid"] = tid
                valid_entries.append(entry)
                logging.debug(f"新增待推送：TID={tid}")
                logging.debug(f"新增待推送：TID={tid}")
            else:
                logging.debug(f"跳过已推送：TID={tid}")
        
        logging.info(f"筛选完成：共{len(valid_entries)}条新帖待推送")
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

# ====================== 5. Markdown转义（不变）======================
def escape_markdown(text):
    special_chars = r"_*~`>#+!()"
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

# ====================== 8. 核心推送逻辑（按TID升序+全量存储）======================
async def check_for_updates():
    # 获取待推送新帖
    rss_entries = fetch_updates()
    if not rss_entries:
        logging.info("无新帖待推送，结束")
        return

    # 按TID升序排序（确保从小到大推送）
    rss_entries_sorted = sorted(rss_entries, key=lambda x: x["tid"])
    logging.info(f"新帖按TID升序：{[e['tid'] for e in rss_entries_sorted]}")

    # 限制单次推送数量
    push_entries = rss_entries_sorted[:MAX_PUSH_PER_RUN]
    logging.info(f"本次推送{len(push_entries)}条：{[e['tid'] for e in push_entries]}")

    # 发送并记录成功的TID
    async with aiohttp.ClientSession() as session:
        # 读取已有推送记录（用于后续合并）
        existing_tids = load_pushed_tids()
        # 记录本次推送成功的TID
        newly_pushed_tids = []
        
        for i, entry in enumerate(push_entries):
            link = entry["link"]
            tid = entry["tid"]
            title = entry.get("title", "无标题").strip()
            author = entry.get("author", "未知用户").strip()

            # 构造文本（用全角＠避免跳转）
            title_escaped = escape_markdown(title)
            author_escaped = escape_markdown(author)
            caption = (
                f"{title_escaped}\n"
                f"由 ＠{author_escaped} 发起的话题讨论\n"
                f"链接：{link}\n\n"
                f"项目地址：{FIXED_PROJECT_URL}"
            )

            # 发送
            images = await get_images_from_webpage(session, link)
            delay = 5 if i > 0 else 0  # 间隔推送避免刷屏
            send_success = False

            if images:
                send_success = await send_photo_with_caption(session, images[0], caption, delay)
            else:
                send_success = await send_text(session, caption, delay)

            if send_success:
                newly_pushed_tids.append(tid)
                logging.info(f"✅ 已推送TID：{tid}")

    # 保存新推送的TID（合并到已有列表并去重）
    if newly_pushed_tids:
        save_pushed_tids(newly_pushed_tids, existing_tids)
    else:
        logging.info("无成功推送的TID，不更新记录")

# ====================== 主函数 =======================
async def main():
    logging.info("===== 推送脚本启动 =====")
    
    # 配置校验
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
        logging.error("❌ 基础配置错误，脚本终止")
        return

    # 执行推送
    try:
        await check_for_updates()
    except Exception as e:
        logging.error(f"❌ 核心逻辑异常：{str(e)}")
    
    logging.info("===== 推送脚本结束 =====")

if __name__ == "__main__":
    asyncio.run(main())
