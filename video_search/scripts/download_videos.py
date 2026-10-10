import requests
from bs4 import BeautifulSoup
import subprocess
import os

# 1. 获取网页内容
def fetch_page(url):
    try:
        response = requests.get(url)
        response.raise_for_status()  # 检查请求是否成功
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"Failed to fetch the page: {e}")
        return None

# 2. 解析视频地址
def extract_video_urls(html_content):
    soup = BeautifulSoup(html_content, 'html.parser')
    video_urls = []

    # 假设视频地址在 <video> 标签的 src 属性中
    for video_tag in soup.find_all('video'):
        if 'src' in video_tag.attrs:
            video_urls.append(video_tag['src'])

    # 如果没有找到 <video> 标签，可以尝试其他标签或属性
    # 例如，某些网站可能将视频地址放在 <a> 标签的 href 属性中
    if not video_urls:
        for a_tag in soup.find_all('a', href=True):
            if a_tag['href'].endswith(('.mp4', '.webm', '.ogg')):  # 常见的视频格式
                video_urls.append(a_tag['href'])

    return video_urls

# 3. 使用 ffmpeg 下载视频
def download_video(video_url, output_dir='downloads'):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 提取视频文件名
    video_name = video_url.split('/')[-1]  # 从URL中提取文件名
    output_path = os.path.join(output_dir, video_name)

    # 使用 ffmpeg 下载视频
    try:
        subprocess.run(['ffmpeg', '-i', video_url, '-c', 'copy', output_path], check=True)
        print(f"Downloaded: {video_name}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to download {video_url}: {e}")

# 主函数
def main():
    # 目标网站URL
    url = 'https://example.com'  # 替换为你要抓取的网站URL

    # 获取网页内容
    html_content = fetch_page(url)
    if not html_content:
        return

    # 解析视频地址
    video_urls = extract_video_urls(html_content)
    if not video_urls:
        print("No video URLs found.")
        return

    print(f"Found {len(video_urls)} video(s).")

    # 下载视频
    for video_url in video_urls:
        download_video(video_url)

if __name__ == '__main__':
    main()