#!/usr/bin/env python3
import argparse
import base64
import binascii
import json
import os
import time
import unicodedata
from io import BytesIO
from typing import Optional, Tuple, Dict, Any, Set
from urllib.parse import urlparse, unquote

from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


class SafeFilename:
    """ファイル名の正規化と安全な生成を担当するクラス"""

    CP932_SAFE_MAP = {
        '麴': '麹', '𠮟': '叱', '𠮷': '吉', '鷗': '鴎', '俱': '倶',
        '剝': '剥', '吞': '呑', '噓': '嘘', '妍': '研', '瘦': '痩',
        '繫': '繋', '髙': '高', '﨑': '崎',
    }
    BAD_CHARS = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
    WHITESPACE_CHARS = ['\n', '\r', '\t']

    @classmethod
    def sanitize(cls, name: str, cp932_safe: bool = True) -> str:
        """ファイル名に使えない文字を置換し、必要に応じてCP932対応を行う"""
        if not name:
            return ''

        for ch in cls.WHITESPACE_CHARS:
            name = name.replace(ch, ' ')
        for ch in cls.BAD_CHARS:
            name = name.replace(ch, '-')

        name = ' '.join(name.split())

        if cp932_safe:
            name = cls._cp932_safe_substitute(name)

        return name.strip()

    @classmethod
    def _cp932_safe_substitute(cls, text: str) -> str:
        """CP932でエンコード可能な文字のみに変換する"""
        out = ''.join(cls.CP932_SAFE_MAP.get(ch, ch) for ch in text)
        try:
            out.encode('cp932')
            return out
        except Exception:
            pass

        out2 = unicodedata.normalize('NFKC', out)
        try:
            out2.encode('cp932')
            return out2
        except Exception:
            return ''.join(ch if cls._is_cp932_encodable(ch) else '〓' for ch in out2)

    @staticmethod
    def _is_cp932_encodable(ch: str) -> bool:
        try:
            ch.encode('cp932')
            return True
        except Exception:
            return False

    @classmethod
    def derive_unique_filepath(cls, url: str, width: int, height: int, archive_dir: str, ext: str) -> str:
        """URLと画像サイズから一意なファイルパスを生成する"""
        parsed = urlparse(url)
        name = os.path.basename(parsed.path)
        name = unquote(name)
        if not name:
            name = f'image_{width}x{height}'

        safe_name = cls.sanitize(name, cp932_safe=True)
        if not safe_name:
            safe_name = f'image_{width}x{height}'

        if not os.path.splitext(safe_name)[1]:
            safe_name = f'{safe_name}{ext}'

        candidate = safe_name
        idx = 1
        while os.path.exists(os.path.join(archive_dir, candidate)):
            root_part, ext_part = os.path.splitext(safe_name)
            candidate = f'{root_part}_{idx}{ext_part}'
            idx += 1

        return os.path.join(archive_dir, candidate)


class ImageSaver:
    """画像の検証と保存を担当するクラス"""

    ALLOWED_FORMATS = {'JPEG', 'JPG', 'PNG', 'GIF'}

    def __init__(self, archive_dir: str, min_size: Tuple[int, int], require_both_sides: bool, debug: bool = False):
        self.archive_dir = archive_dir
        self.min_long_side = max(min_size)
        self.min_short_side = min(min_size)
        self.require_both_sides = require_both_sides
        self.debug = debug
        os.makedirs(self.archive_dir, exist_ok=True)

    def meets_requirement(self, width: int, height: int) -> bool:
        long_side = max(width, height)
        short_side = min(width, height)
        if self.require_both_sides:
            return long_side >= self.min_long_side and short_side >= self.min_short_side
        return long_side >= self.min_long_side

    def _svg_to_png_bytes(self, data: bytes) -> Optional[bytes]:
        try:
            import cairosvg
        except Exception:
            print('SVG変換エラー: cairosvg がインストールされていません')
            return None

        try:
            return cairosvg.svg2png(bytestring=data)
        except Exception as e:
            print(f'SVG変換エラー: {e}')
            return None

    def save_from_bytes(self, data: bytes, url: str, mime_type: Optional[str] = '') -> None:
        mime_value = (mime_type or '').lower()
        is_svg = mime_value == 'image/svg+xml'
        if is_svg:
            png_bytes = self._svg_to_png_bytes(data)
            if not png_bytes:
                return
            data = png_bytes
            mime_type = 'image/png'

        try:
            image = Image.open(BytesIO(data))
        except Exception as e:
            data_len = len(data) if isinstance(data, (bytes, bytearray)) else 0
            head_hex = ''
            if isinstance(data, (bytes, bytearray)):
                head_hex = binascii.hexlify(data[:16]).decode('ascii')
            print(f'画像読み込みエラー: {e}')
            print(f'  url={url}')
            print(f'  mime={mime_type}')
            print(f'  data_len={data_len}')
            print(f'  head_hex={head_hex}')
            return

        fmt = (image.format or '').upper()
        if not fmt and mime_type:
            fmt = mime_type.split('/')[-1].upper()

        if fmt not in self.ALLOWED_FORMATS:
            if self.debug:
                print(f'保存対象外の形式: fmt={fmt} mime={mime_type} url={url}')
            return

        if not self.meets_requirement(image.width, image.height):
            if self.debug:
                print(f'サイズ不足でスキップ: {image.width}x{image.height} url={url}')
            return

        ext = f'.{fmt.lower()}' if fmt != 'JPEG' else '.jpg'
        filepath = SafeFilename.derive_unique_filepath(url, image.width, image.height, self.archive_dir, ext)

        try:
            image.save(filepath, format=fmt if fmt != 'JPG' else 'JPEG')
            print(f'Saved: {os.path.basename(filepath)} ({image.width}x{image.height})')
        except Exception as e:
            print(f'保存失敗: {os.path.basename(filepath)} - {e}')


class BrowserSession:
    """Seleniumブラウザセッションを管理するコンテキストマネージャ"""

    def __init__(self, profile_dir_name: str = '.chrome', disable_cache: bool = False):
        self.profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), profile_dir_name)
        self.driver: Optional[webdriver.Chrome] = None
        self.disable_cache = disable_cache

    def __enter__(self) -> 'BrowserSession':
        options = Options()
        options.add_argument(f"--user-data-dir={self.profile_dir}")

        # CDP Networkイベント取得用設定
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        self.driver = webdriver.Chrome(options=options)

        # 大きな画像を扱うためにバッファサイズを拡張
        self.execute_cdp_cmd('Network.enable', {
            'maxTotalBufferSize': 200 * 1024 * 1024,
            'maxResourceBufferSize': 100 * 1024 * 1024,
        })
        if self.disable_cache:
            self.execute_cdp_cmd('Network.setCacheDisabled', {'cacheDisabled': True})
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.driver:
            self.driver.quit()

    def execute_cdp_cmd(self, cmd: str, params: Dict[str, Any]) -> Any:
        return self.driver.execute_cdp_cmd(cmd, params)

    def get_performance_log(self):
        return self.driver.get_log('performance')

    def get_response_body(self, request_id: str) -> Optional[bytes]:
        try:
            res = self.execute_cdp_cmd('Network.getResponseBody', {'requestId': request_id})
            body = res.get('body', '')
            if res.get('base64Encoded'):
                return base64.b64decode(body)
            return body.encode('utf-8')
        except Exception as e:
            print(f'CDP getResponseBody error: {e}')
            return None


class BrowsingCaptureApp:
    """アプリケーションのメインロジック"""

    def __init__(self, archive_dir: str, min_size_str: str, debug: bool = False, disable_cache: bool = False):
        min_size, require_both_sides = self._parse_size(min_size_str)
        self.saver = ImageSaver(archive_dir, min_size, require_both_sides, debug=debug)
        self.seen_urls: Set[str] = set()
        self.pending_requests: Dict[str, Dict[str, Any]] = {}
        self.debug = debug
        self.disable_cache = disable_cache

    @staticmethod
    def _parse_size(text: str) -> Tuple[Tuple[int, int], bool]:
        if 'x' not in text.lower():
            try:
                value = int(text)
                if value <= 0:
                    raise ValueError
                return (value, value), False
            except ValueError:
                raise ValueError('サイズは WIDTHxHEIGHT 形式か数値を指定してください (例: 480x320 / 480)')
        try:
            w_str, h_str = text.lower().split('x', 1)
            w, h = int(w_str), int(h_str)
            if w <= 0 or h <= 0:
                raise ValueError
            return (w, h), True
        except ValueError:
            raise ValueError('サイズは WIDTHxHEIGHT 形式か数値を指定してください (例: 480x320 / 480)')

    def run(self):
        print('ブラウザを起動しています...')
        with BrowserSession(disable_cache=self.disable_cache) as session:
            print(f'保存先: {self.saver.archive_dir}')
            print('ブラウザで閲覧中の画像が自動保存されます。終了は Ctrl+C を押してください。')

            try:
                while True:
                    self._process_logs(session)
                    time.sleep(0.2)
            except KeyboardInterrupt:
                print('\n終了します')

    def _process_logs(self, session: BrowserSession):
        try:
            logs = session.get_performance_log()
        except Exception:
            return

        for entry in logs:
            try:
                message = json.loads(entry['message'])['message']
            except (KeyError, json.JSONDecodeError):
                continue

            method = message.get('method')
            params = message.get('params', {})

            if method == 'Network.requestWillBeSent':
                self._handle_request(params)
            elif method == 'Network.responseReceived':
                self._handle_response(params)
            elif method == 'Network.loadingFinished':
                self._handle_finished(session, params)
            elif method == 'Network.loadingFailed':
                self._handle_failed(params)

    def _handle_request(self, params: Dict[str, Any]):
        req_id = params.get('requestId')
        url = params.get('request', {}).get('url')
        req_type = params.get('type')
        if req_id and url:
            info = self.pending_requests.setdefault(req_id, {})
            info['url'] = url
            if req_type:
                info['type'] = req_type
                if self.debug and req_type == 'Image':
                    print(f'画像リクエスト: url={url}')

    def _handle_response(self, params: Dict[str, Any]):
        mime = params.get('response', {}).get('mimeType', '')
        if not mime.startswith('image/'):
            return

        req_id = params.get('requestId')
        url = params.get('response', {}).get('url')
        if req_id and url:
            info = self.pending_requests.setdefault(req_id, {})
            info['mime'] = mime
            info['url'] = url
            if self.debug:
                print(f'画像レスポンス: url={url} mime={mime}')

    def _handle_finished(self, session: BrowserSession, params: Dict[str, Any]):
        req_id = params.get('requestId')
        if not req_id:
            return

        info = self.pending_requests.get(req_id)
        if not info:
            return

        # 必要な情報が揃っているか確認（mimeはresponseで設定される）
        url = info.get('url')
        if not url or url in self.seen_urls:
            if self.debug and url:
                print(f'重複URLでスキップ: {url}')
            self.pending_requests.pop(req_id, None)
            return

        mime = info.get('mime')
        if not mime:
            if info.get('type') != 'Image':
                # まだレスポンス情報が来ていない、または画像ではない
                self.pending_requests.pop(req_id, None)
                return
            if self.debug:
                print(f'画像レスポンスのMIME未取得: url={url}')

        self.seen_urls.add(url)

        # 画像データ取得
        data = session.get_response_body(req_id)
        if data:
            self.saver.save_from_bytes(data, url, mime)
        elif self.debug:
            print(f'画像データ取得失敗: url={url} mime={mime}')

        self.pending_requests.pop(req_id, None)

    def _handle_failed(self, params: Dict[str, Any]):
        req_id = params.get('requestId')
        if req_id in self.pending_requests:
            self.pending_requests.pop(req_id, None)


def main():
    parser = argparse.ArgumentParser(description='ブラウザ操作中に表示された画像を保存します')
    parser.add_argument('archive_dir', help='保存先ディレクトリ')
    parser.add_argument(
        '--min',
        default='480x320',
        help='保存する最小画像サイズ。WIDTHxHEIGHT 形式は両辺条件、数値のみは片辺条件 (例: 480x320 / 480)'
    )
    parser.add_argument('--debug', action='store_true', help='保存しない理由を表示する')
    parser.add_argument('--no-cache', action='store_true', help='キャッシュを無効化して取得を強制する')
    args = parser.parse_args()

    try:
        app = BrowsingCaptureApp(args.archive_dir, args.min, debug=args.debug, disable_cache=args.no_cache)
        app.run()
    except Exception as e:
        print(f'エラーが発生しました: {e}')


if __name__ == '__main__':
    main()
