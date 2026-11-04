"""
FLATON（フラットン）東京発→高知行き 空席監視スクリプト
発車オーライネットの検索結果を確認し、満席から空きが出たら LINE に通知します。

環境変数:
  TARGET_DATES               監視する乗車日（カンマ区切り, YYYYMMDD）例: 20261226,20261227
  LINE_CHANNEL_ACCESS_TOKEN  LINE Messaging API のチャネルアクセストークン
  LINE_USER_ID               通知先の LINE ユーザーID（U から始まる文字列）
  TEST_NOTIFY                "true" ならテスト通知を1通送るだけで終了
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
STATE_FILE = "state.json"

# 発車オーライネットの路線コード（FLATON 高知・鳴門⇔東京）
ROUTE_PARAMS = {"gpcd": "390007", "rocd": "0001", "uncd": "3905"}
LIST_URL = "https://secure.j-bus.co.jp/hon/Route/ListDate"
PAGE_URL = "https://secure.j-bus.co.jp/hon/Route/Highway"

DEPART_KEYWORD = "東京"  # 出発停留所に含まれる文字
ARRIVE_KEYWORD = "高知"  # 到着停留所に含まれる文字

HEADERS = {
    "User-Agent": "Mozilla/5.0 (personal seat-availability checker; 5-10 min interval)",
    "Accept-Language": "ja",
}


def fetch_status(date: str) -> list[dict]:
    """指定日の東京発→高知行きの便の空席状況を返す。"""
    params = {
        "random": str(int(time.time() * 1000)),
        "dpcd": "", "apcd": "",
        "dtym": date[:6], "dtdd": date[6:],
        "bncd": "", "around": "0", "updn": "", "bscd": "",
        **ROUTE_PARAMS,
    }
    r = requests.get(LIST_URL, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    for art in soup.select("article.routeinfo"):
        stops = [s.get_text(strip=True) for s in art.select("span.js-stop-name")]
        if not stops:
            continue
        if DEPART_KEYWORD not in stops[0] or ARRIVE_KEYWORD not in stops[-1]:
            continue
        label = art.select_one("p.seat [class*=label-seat]")
        status = label.get_text(strip=True) if label else "不明"
        bin_text = ""
        for p in art.select("p"):
            t = " ".join(p.get_text().split())
            if "便" in t and "号車" in t:
                bin_text = t
                break
        dep_time = art.select_one("span.js-stop-time")
        results.append({
            "bin": bin_text,
            "status": status,
            "from": stops[0],
            "to": stops[-1],
            "dep_time": dep_time.get_text(strip=True) if dep_time else "",
        })
    return results


def send_line(text: str) -> None:
    token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    user_id = os.environ["LINE_USER_ID"]
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": text[:4900]}]},
        timeout=30,
    )
    if r.status_code >= 300:
        print(f"LINE送信失敗: {r.status_code} {r.text}", file=sys.stderr)
        r.raise_for_status()


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def booking_url(date: str) -> str:
    q = "&".join(f"{k}={v}" for k, v in ROUTE_PARAMS.items())
    return f"{PAGE_URL}?{q}&dtym={date[:6]}&dtdd={date[6:]}"


def main() -> None:
    if os.environ.get("TEST_NOTIFY", "").lower() == "true":
        send_line("✅ FLATON空席監視のテスト通知です。この通知が届けば設定は完了です。")
        print("テスト通知を送信しました")
        return

    now = datetime.now(JST)
    # 発車オーライネットの利用可能時間は 5:00〜26:00（翌2:00）
    if 2 <= now.hour < 5:
        print("サイトの利用時間外（2:00〜5:00）のためスキップ")
        return

    dates = [d.strip() for d in os.environ.get("TARGET_DATES", "").split(",") if d.strip()]
    if not dates:
        print("TARGET_DATES が設定されていません", file=sys.stderr)
        sys.exit(1)

    state = load_state()
    for date in dates:
        try:
            buses = fetch_status(date)
        except Exception as e:  # 一時的なエラーは次回に再確認
            print(f"{date}: 取得失敗 {e}", file=sys.stderr)
            continue

        if not buses:
            print(f"{date}: 東京発の便が見つかりません（予約受付前・終了・運休の可能性）")
            continue

        for bus in buses:
            key = f"{date}|{bus['bin']}"
            prev = state.get(key)
            cur = bus["status"]
            print(f"{date} {bus['bin']} {bus['dep_time']} {bus['from']}→{bus['to']}: {cur}（前回: {prev}）")

            available = cur not in ("満席", "不明")
            was_available = prev not in (None, "満席", "不明")
            if available and not was_available:
                d = datetime.strptime(date, "%Y%m%d")
                send_line(
                    f"🚌 FLATONに空きが出ました！\n"
                    f"{d.month}/{d.day} {bus['dep_time']} {bus['from']} → {bus['to']}\n"
                    f"{bus['bin']}　状態: {cur}\n\n"
                    f"今すぐ予約👇\n{booking_url(date)}"
                )
                print("→ LINE通知を送信")
            state[key] = cur

    save_state(state)


if __name__ == "__main__":
    main()
