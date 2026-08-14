# -*- coding: utf-8 -*-
"""
PDF 下载和解析模块
"""
import os
import re
import hashlib
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse, parse_qs
import logging

logger = logging.getLogger(__name__)


class PDFProcessor:
    """PDF 处理器"""

    def __init__(self, download_dir: str = None):
        """
        初始化 PDF 处理器

        Args:
            download_dir: PDF 下载目录，如果为 None 则在 process 方法中动态指定
        """
        self._default_download_dir = download_dir
        self._current_download_dir = None

    def set_download_dir(self, download_dir: str):
        """设置当前下载目录（用于批次处理）"""
        self._current_download_dir = Path(download_dir)
        self._current_download_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"PDF 下载目录设置为: {self._current_download_dir}")

    def _get_download_dir(self) -> Path:
        """获取当前下载目录"""
        if self._current_download_dir:
            return self._current_download_dir
        if self._default_download_dir:
            path = Path(self._default_download_dir)
            path.mkdir(parents=True, exist_ok=True)
            return path
        # 默认目录
        path = Path("output/data/pdfs")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def extract_pdf_url(self, detail_url: str, html_content: str = None) -> Optional[str]:
        """
        从详情页提取 PDF 下载链接

        巨潮资讯网的 PDF URL 格式：
        http://static.cninfo.com.cn/finalpage/2026-07-29/1225444806.PDF
        """
        # 方法1：从 URL 参数中提取 announcementId
        parsed = urlparse(detail_url)
        params = parse_qs(parsed.query)
        announcement_id = params.get('announcementId', [None])[0]

        if announcement_id:
            # 构建 PDF URL（需要日期）
            announcement_time = params.get('announcementTime', [None])[0]
            if announcement_time:
                return f"http://static.cninfo.com.cn/finalpage/{announcement_time}/{announcement_id}.PDF"

        # 方法2：从 HTML 中解析（备用）
        if html_content:
            # 匹配常见的 PDF 链接模式
            patterns = [
                r'href=["\']([^"\']*\.PDF)["\']',
                r'href=["\']([^"\']*\.pdf)["\']',
                r'src=["\']([^"\']*static\.cninfo\.com\.cn[^"\']*)["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html_content, re.IGNORECASE)
                if match:
                    pdf_url = match.group(1)
                    if not pdf_url.startswith('http'):
                        pdf_url = 'http://static.cninfo.com.cn' + pdf_url
                    return pdf_url

        return None

    def download_pdf(self, pdf_url: str, stock_code: str = "688111", extra_headers: dict = None) -> Optional[Tuple[str, str]]:
        """
        下载 PDF 文件

        Args:
            pdf_url: PDF 下载链接
            stock_code: 股票代码（用于命名）
            extra_headers: 额外的请求头（如 Referer，用于绕过防盗链）

        Returns:
            (本地文件路径, 文件ID)，失败返回 None
        """
        try:
            import requests

            # 生成文件ID（基于URL）
            file_id = hashlib.md5(pdf_url.encode()).hexdigest()[:12]

            # 获取下载目录
            download_dir = self._get_download_dir()

            # 构建保存路径
            pdf_path = download_dir / f"{stock_code}_{file_id}.pdf"

            # 如果已存在，直接返回
            if pdf_path.exists():
                logger.info(f"PDF 已存在: {pdf_path}")
                return str(pdf_path), file_id

            # 下载
            logger.info(f"下载 PDF: {pdf_url}")
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/pdf,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            }
            if extra_headers:
                headers.update(extra_headers)

            # 针对上交所的特殊处理
            if 'sse.com.cn' in pdf_url:
                headers.update({
                    'Sec-Fetch-Site': 'same-origin',
                    'Sec-Fetch-Mode': 'no-cors',
                })

            response = requests.get(pdf_url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()

            # 检查内容类型
            content_type = response.headers.get('Content-Type', '')
            logger.debug(f"  Content-Type: {content_type}")

            # 保存
            with open(pdf_path, 'wb') as f:
                f.write(response.content)

            logger.info(f"PDF 下载成功: {pdf_path} ({len(response.content)} bytes)")
            return str(pdf_path), file_id

        except OSError as e:
            logger.error(f"PDF 下载失败: {e}")
            return None

    def extract_text(self, pdf_path: str, max_pages: int = 10) -> Optional[str]:
        """
        从 PDF 提取文本

        Args:
            pdf_path: PDF 文件路径
            max_pages: 最多解析多少页（防止超长文档）

        Returns:
            提取的文本内容
        """
        # 尝试使用 pymupdf (fitz)
        text = self._extract_with_pymupdf(pdf_path, max_pages)
        if text and len(text.strip()) > 100:
            return text

        # 备选：pdfplumber
        text = self._extract_with_pdfplumber(pdf_path, max_pages)
        if text and len(text.strip()) > 100:
            return text

        logger.warning(f"PDF 文本提取失败或内容太短: {pdf_path}")
        return None

    def _extract_with_pymupdf(self, pdf_path: str, max_pages: int) -> Optional[str]:
        """使用 PyMuPDF 提取文本"""
        try:
            import fitz  # PyMuPDF

            text_parts = []
            with fitz.open(pdf_path) as doc:
                for i, page in enumerate(doc):
                    if i >= max_pages:
                        break
                    text = page.get_text()
                    if text.strip():
                        text_parts.append(text)

            return '\n'.join(text_parts) if text_parts else None

        except ImportError:
            logger.debug("PyMuPDF 未安装")
            return None
        except Exception as e:
            logger.debug(f"PyMuPDF 提取失败: {e}")
            return None

    def _extract_with_pdfplumber(self, pdf_path: str, max_pages: int) -> Optional[str]:
        """使用 pdfplumber 提取文本"""
        try:
            import pdfplumber

            text_parts = []
            with pdfplumber.open(pdf_path) as pdf:
                for i, page in enumerate(pdf.pages):
                    if i >= max_pages:
                        break
                    text = page.extract_text()
                    if text and text.strip():
                        text_parts.append(text)

            return '\n'.join(text_parts) if text_parts else None

        except ImportError:
            logger.debug("pdfplumber 未安装")
            return None
        except Exception as e:
            logger.debug(f"pdfplumber 提取失败: {e}")
            return None

    def process(self, detail_url: str, stock_code: str = "688111", html_content: str = None) -> Optional[Tuple[str, str]]:
        """
        完整处理流程：提取PDF链接 -> 下载 -> 解析文本

        Returns:
            (PDF本地路径, 提取的文本内容)，失败返回 None
        """
        # 提取 PDF URL
        pdf_url = self.extract_pdf_url(detail_url, html_content)
        if not pdf_url:
            logger.warning(f"无法提取 PDF URL: {detail_url}")
            return None

        # 下载
        result = self.download_pdf(pdf_url, stock_code)
        if not result:
            return None

        pdf_path, file_id = result

        # 提取文本
        text = self.extract_text(pdf_path)

        return pdf_path, text

    def cleanup(self, max_age_days: int = 7):
        """清理过期的 PDF 文件（仅在默认目录下清理）"""
        import time

        # 只在默认目录清理，不清理批次目录
        if self._current_download_dir or not self._default_download_dir:
            logger.debug("跳过清理（使用批次目录或未设置默认目录）")
            return

        download_dir = Path(self._default_download_dir)
        if not download_dir.exists():
            return

        cutoff = time.time() - (max_age_days * 24 * 3600)
        deleted = 0

        for pdf_file in download_dir.glob("*.pdf"):
            if pdf_file.stat().st_mtime < cutoff:
                pdf_file.unlink()
                deleted += 1

        if deleted > 0:
            logger.info(f"清理了 {deleted} 个过期 PDF 文件")
