#!/usr/bin/env python3
import argparse
import base64
import json
import os
import time
import unicodedata
from io import BytesIO
from urllib.parse import urlparse, unquote, urlsplit, urlunsplit

from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# --- filename_utils parts ---

_CP932_SAFE_FILENAMES = False

CP932_SAFE_MAP = {
    '麴': '麹',  # U+9EB4(NG) -> U+9EB9(OK)
    '𠮟': '叱',  # U+20B9F(NG) -> U+53F1(OK)
    '𠮷': '吉',  # U+20BB7(NG) -> U+5409(OK)
    '鷗': '鴎',  # U+9DD7(NG) -> U+9D0E(OK)
    '俱': '倶',  # U+4FF1(NG) -> U+5036(OK)
    '剝': '剥',  # U+5265(NG) -> U+5265(OK) ※剥はCP932にあるが、剝(U+5265)はNG
    '吞': '呑',  # U+541E(NG) -> U+5451(OK)
    '噓': '嘘',  # U+5618(NG) -> U+5618(OK) ※嘘はCP932にあるが、噓(U+5618)はNG
    '妍': '研',  # U+59F8(NG) -> U+7814(OK) ※妍はCP932にない。研で代用
    '瘦': '痩',  # U+75E9(NG) -> U+75E9(OK) ※痩はCP932にあるが、瘦(U+75E9)はNG
    '繫': '繋',  # U+7E6B(NG) -> U+7E4B(OK)
    '髙': '高',  # U+9AD9(OK) -> U+9AD8(OK) ※正規化
    '﨑': '崎',  # U+FA11(OK) -> U+5D0E(OK) ※正規化
}

def sanitize_filename(name, cp932_safe=None):
    """ファイル名に使えない/問題のある文字を安全に正規化する。"""
    if not name:
        return ''
    # 禁止/問題文字
    bad_chars = ['\n', '\r', '\t']
    for ch in bad_chars:
        name = name.replace(ch, ' ')
    bad_chars = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
    for ch in bad_chars:
        name = name.replace(ch, '-')
    # スペース正規化
    name = ' '.join(name.split())
    # CP932 セーフ置換（必要時）
    apply_cp932 = cp932_safe if cp932_safe is not None else _CP932_SAFE_FILENAMES
    if apply_cp932:
        name = _cp932_safe_substitute(name)
    return name.strip()

def _cp932_safe_substitute(text):
    # まず既知マップで置換
    out = ''.join(CP932_SAFE_MAP.get(ch, ch) for ch in text)
    # それでもcp932に載らない文字は、NFKCで正規化し、非対応文字は代替記号（〓）に置換
    try:
        out.encode('cp932')
        return out
    except Exception:
        out2 = unicodedata.normalize('NFKC', out)
        try:
            out2.encode('cp932')
            return out2
        except Exception:
            # cp932非対応の文字は 〓 (U+3013) に置換
            out3 = ''.join(ch if _is_cp932_encodable(ch) else '〓' for ch in out2)
            return out3

def _is_cp932_encodable(ch):
    try:
        ch.encode('cp932')
        return True
    except Exception:
        return False

# --- seleniumHelper parts ---

def getBrowserSession(enable_cdp=False, start_cdp_collector=True):
    # Note: start_cdp_collector is ignored in this single-file version as we don't include the collector class.
    # The original script passed False anyway.

    profile = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.chrome')
    options = Options()
    options.add_argument(f"--user-data-dir={profile}")
    if enable_cdp:
        # CDPのNetworkイベントを取得するためにperformanceログを有効化する。
        options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    browser = webdriver.Chrome(options=options)

    if enable_cdp:
        browser.execute_cdp_cmd('Network.enable', {
            # 既定の32MB/8MBでは大きなPDFがキャッシュから溢れるため十分に大きな値を設定
            'maxTotalBufferSize': 200 * 1024 * 1024,
            'maxResourceBufferSize': 100 * 1024 * 1024,
        })

    return browser

def closeBrowser(browser):
    browser.quit()

# --- save-while-browsing.py parts ---

def parse_size(text):
    if 'x' not in text.lower():
        raise ValueError('サイズはWIDTHxHEIGHT形式で指定してください (例: 480x320)')
    w_str, h_str = text.lower().split('x', 1)
    w = int(w_str)
    h = int(h_str)
    if w <= 0 or h <= 0:
        raise ValueError('幅と高さは正の整数で指定してください')
    long_side = max(w, h)
    short_side = min(w, h)
    return long_side, short_side


def meets_requirement(size, req_long, req_short):
    width, height = size
    long_side = max(width, height)
    short_side = min(width, height)
    return long_side >= req_long and short_side >= req_short


def derive_filename(url, width, height, archive_dir, default_ext):
    parsed = urlparse(url)
    name = os.path.basename(parsed.path)
    name = unquote(name)
    if not name:
        name = f'image_{width}x{height}'
    safe = sanitize_filename(name, cp932_safe=True)
    if not safe:
        safe = f'image_{width}x{height}'
    if not os.path.splitext(safe)[1]:
        safe = f'{safe}{default_ext}'

    candidate = safe
    idx = 1
    while os.path.exists(os.path.join(archive_dir, candidate)):
        root, ext = os.path.splitext(safe)
        candidate = f'{root}_{idx}{ext}'
        idx += 1
    return candidate


def save_image(bytes_data, path, format_hint):
    image = Image.open(BytesIO(bytes_data))
    image.save(path, format=image.format or format_hint)


def main():
    parser = argparse.ArgumentParser(description='ブラウザ操作中に表示された画像を保存します')
    parser.add_argument('archive_dir', help='保存先ディレクトリ')
    parser.add_argument('--min', default='480x320', help='保存する最小画像サイズ (デフォルト: 480x320)')
    args = parser.parse_args()

    req_long, req_short = parse_size(args.min)
    archive_dir = os.path.abspath(args.archive_dir)
    os.makedirs(archive_dir, exist_ok=True)

    browser = getBrowserSession(enable_cdp=True, start_cdp_collector=False)
    print('ブラウザが起動しました。保存したいページを自由に操作してください。Ctrl+Cで終了します。')
    print(f'保存条件: 長辺>= {req_long}px かつ 短辺>= {req_short}px / 保存先: {archive_dir}')

    seen_urls = set()
    pending = {}
    allowed_formats = {'JPEG', 'JPG', 'PNG', 'GIF'}
    current_page_url = None

    def process_if_ready(req_id):
        info = pending.get(req_id)
        if not info or not info.get('finished'):
            return
        url = info.get('url')
        mime = info.get('mime', '')
        pending.pop(req_id, None)
        if not url:
            return
        if not mime.startswith('image/'):
            return
        if url in seen_urls:
            return
        seen_urls.add(url)

        parsed = urlparse(url)
        tail_name = unquote(os.path.basename(parsed.path)) or url
        display_name = tail_name
        format_from_mime = mime.split('/')[-1].upper() if mime else ''
        if format_from_mime and format_from_mime not in allowed_formats:
            print(f'　{display_name}')
            return

        try:
            body = browser.execute_cdp_cmd('Network.getResponseBody', {'requestId': req_id})
        except Exception as e:
            print(f'　{display_name}')
            print(f'CDP取得失敗: {e}')
            return

        data = body.get('body', '')
        if body.get('base64Encoded'):
            try:
                data = base64.b64decode(data)
            except Exception as e:
                print(f'　{display_name}')
                print(f'base64デコード失敗: {e}')
                return
        else:
            data = data.encode('utf-8')

        try:
            image = Image.open(BytesIO(data))
        except Exception as e:
            print(f'　{display_name}')
            print(f'画像として開けませんでした: {e}')
            return

        format_name = (image.format or '').upper() or format_from_mime
        if format_name not in allowed_formats:
            print(f'　{display_name}')
            return

        if not meets_requirement(image.size, req_long, req_short):
            print(f'　{display_name}')
            return

        ext = f'.{(image.format or "jpg").lower()}'
        filename = derive_filename(url, image.width, image.height, archive_dir, ext)
        path = os.path.join(archive_dir, filename)
        display_name = filename
        print(f'　{display_name}')
        try:
            image.save(path, format=image.format or 'JPEG')
        except Exception as e:
            print(f'保存に失敗しました: {e}')
            return
        print(f'　　{image.width}x{image.height} Saved: {filename}')

    try:
        while True:
            time.sleep(0.2)
            try:
                entries = browser.get_log('performance')
            except Exception as e:
                print(f'performanceログの取得に失敗: {e}')
                continue
            for entry in entries:
                try:
                    message = json.loads(entry['message'])['message']
                except (KeyError, json.JSONDecodeError):
                    print('ログのJSON解析に失敗しました')
                    continue

                method = message.get('method')
                params = message.get('params', {})

                if method == 'Network.requestWillBeSent':
                    request = params.get('request', {})
                    req_id = params.get('requestId')
                    url = request.get('url')
                    if params.get('type') == 'Document' and url:
                        if url != current_page_url:
                            print(url)
                            current_page_url = url
                    if req_id and url:
                        info = pending.setdefault(req_id, {})
                        info['url'] = url
                    continue

                if method == 'Network.responseReceived':
                    response = params.get('response', {})
                    mime = response.get('mimeType', '')
                    if not mime.startswith('image/'):
                        continue
                    req_id = params.get('requestId')
                    url = response.get('url')
                    if not req_id or not url:
                        print('画像レスポンスに必要情報がありません')
                        continue
                    info = pending.setdefault(req_id, {})
                    info['mime'] = mime
                    info['url'] = url
                    process_if_ready(req_id)
                    continue

                if method == 'Network.loadingFinished':
                    req_id = params.get('requestId')
                    if not req_id:
                        continue
                    info = pending.setdefault(req_id, {})
                    info['finished'] = True
                    process_if_ready(req_id)
                    continue

                if method == 'Network.loadingFailed':
                    req_id = params.get('requestId')
                    error_text = params.get('errorText')
                    if req_id in pending:
                        pending.pop(req_id, None)
                        if error_text:
                            print(f'リクエスト失敗: {error_text}')
                    continue
    except KeyboardInterrupt:
        print('\n終了します')
    finally:
        closeBrowser(browser)


if __name__ == '__main__':
    main()
