"""유튜브 채널(특히 쇼츠) 분석기.

공식 YouTube Data API v3만 사용한다. 크롤링/스크래핑 없음.

사용법:
    export YOUTUBE_API_KEY="..."
    python3 youtube/analyze_channel.py "@행복의나침반"
    python3 youtube/analyze_channel.py "@행복의나침반" --md report.md

API 키 발급: console.cloud.google.com 에서 프로젝트 생성 →
"YouTube Data API v3" 사용 설정 → 사용자 인증 정보에서 API 키 생성.
무료 할당량은 하루 10,000 units 이며, 이 스크립트는 영상 200개 기준
약 30 units 정도만 쓴다.
"""

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import requests

API = "https://www.googleapis.com/youtube/v3"

# 쇼츠 판정 기준: 업로드 시점에 따라 60초 또는 3분까지 허용된다.
# 여기서는 넉넉하게 3분 이하를 쇼츠로 본다.
SHORTS_MAX_SECONDS = 180

KST = timezone(timedelta(hours=9))
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


class ApiError(RuntimeError):
    pass


def _get(path, key, **params):
    params["key"] = key
    r = requests.get(f"{API}/{path}", params=params, timeout=30)
    if r.status_code != 200:
        try:
            reason = r.json()["error"]["message"]
        except Exception:
            reason = r.text[:300]
        raise ApiError(f"{path} {r.status_code}: {reason}")
    return r.json()


def parse_duration(iso):
    """ISO-8601 duration(PT1M5S)을 초로 바꾼다."""
    m = re.fullmatch(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or ""
    )
    if not m:
        return 0
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def resolve_channel(target, key):
    """@handle / channel id / URL 을 채널 리소스로 바꾼다."""
    target = target.strip().rstrip("/")
    if "youtube.com" in target:
        m = re.search(r"/(@[^/?]+)", target)
        if m:
            target = requests.utils.unquote(m.group(1))
        else:
            m = re.search(r"/channel/(UC[\w-]+)", target)
            if m:
                target = m.group(1)

    part = "snippet,statistics,contentDetails"
    if target.startswith("@"):
        data = _get("channels", key, part=part, forHandle=target)
    elif target.startswith("UC"):
        data = _get("channels", key, part=part, id=target)
    else:
        data = _get("channels", key, part=part, forHandle="@" + target)

    items = data.get("items") or []
    if not items:
        raise ApiError(f"채널을 찾을 수 없음: {target}")
    return items[0]


def fetch_videos(uploads_playlist, key, limit):
    """업로드 플레이리스트에서 영상 ID를 최신순으로 모은다."""
    ids, page = [], None
    while len(ids) < limit:
        data = _get(
            "playlistItems",
            key,
            part="contentDetails",
            playlistId=uploads_playlist,
            maxResults=50,
            **({"pageToken": page} if page else {}),
        )
        ids += [i["contentDetails"]["videoId"] for i in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break

    ids = ids[:limit]
    videos = []
    for i in range(0, len(ids), 50):
        data = _get(
            "videos",
            key,
            part="snippet,statistics,contentDetails",
            id=",".join(ids[i : i + 50]),
        )
        for v in data.get("items", []):
            st = v.get("statistics", {})
            sn = v["snippet"]
            videos.append(
                {
                    "id": v["id"],
                    "title": sn["title"],
                    "desc": sn.get("description", ""),
                    "tags": sn.get("tags", []),
                    "published": datetime.fromisoformat(
                        sn["publishedAt"].replace("Z", "+00:00")
                    ).astimezone(KST),
                    "seconds": parse_duration(v["contentDetails"]["duration"]),
                    "views": int(st.get("viewCount", 0)),
                    "likes": int(st.get("likeCount", 0)) if "likeCount" in st else None,
                    "comments": int(st.get("commentCount", 0)),
                }
            )
    videos.sort(key=lambda v: v["published"], reverse=True)
    return videos


def pct(values, p):
    if not values:
        return 0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def cadence(videos, days):
    """최근 N일 업로드 개수와 하루 평균."""
    cut = datetime.now(KST) - timedelta(days=days)
    recent = [v for v in videos if v["published"] >= cut]
    return len(recent), len(recent) / days


def title_keywords(videos, top=15):
    stop = {
        "그리고", "하지만", "그래서", "이것", "저것", "우리", "당신", "너무",
        "정말", "진짜", "shorts", "쇼츠", "구독", "좋아요",
    }
    c = Counter()
    for v in videos:
        for w in re.findall(r"[가-힣]{2,}|[A-Za-z]{3,}", v["title"]):
            wl = w.lower()
            if wl not in stop:
                c[w] += 1
    return c.most_common(top)


def build_report(ch, videos):
    sn, st = ch["snippet"], ch["statistics"]
    shorts = [v for v in videos if v["seconds"] <= SHORTS_MAX_SECONDS]
    longs = [v for v in videos if v["seconds"] > SHORTS_MAX_SECONDS]
    pool = shorts or videos
    views = [v["views"] for v in pool]

    L = []
    add = L.append

    add(f"# 채널 분석: {sn['title']}")
    add("")
    add(f"- 채널 ID: `{ch['id']}`")
    add(f"- 개설일: {sn['publishedAt'][:10]}")
    subs = st.get("subscriberCount")
    add(f"- 구독자: {int(subs):,}" if subs else "- 구독자: 비공개")
    add(f"- 총 조회수: {int(st.get('viewCount', 0)):,}")
    add(f"- 총 영상 수: {int(st.get('videoCount', 0)):,}")
    add(f"- 수집한 영상: {len(videos)}개 (쇼츠 {len(shorts)} / 롱폼 {len(longs)})")
    if sn.get("description"):
        add("")
        add("**채널 소개**")
        add("> " + sn["description"].strip().replace("\n", "\n> "))

    add("")
    add("## 업로드 주기")
    add("")
    add("| 구간 | 업로드 | 하루 평균 |")
    add("|---|---:|---:|")
    for d in (7, 14, 30, 90):
        n, per = cadence(videos, d)
        add(f"| 최근 {d}일 | {n}개 | {per:.2f}개 |")

    if len(videos) >= 2:
        span = (videos[0]["published"] - videos[-1]["published"]).days or 1
        add("")
        add(f"수집 구간 {span}일 동안 전체 평균 {len(videos) / span:.2f}개/일.")

    add("")
    add("## 조회수 분포 (쇼츠 기준)")
    add("")
    if views:
        add(f"- 중앙값: {int(pct(views, 50)):,}")
        add(f"- 평균: {int(sum(views) / len(views)):,}")
        add(f"- 상위 10%: {int(pct(views, 90)):,}")
        add(f"- 최고: {max(views):,}")
        add(f"- 최저: {min(views):,}")
        med = pct(views, 50)
        hits = [v for v in pool if med and v["views"] >= med * 5]
        add(f"- 중앙값 5배 이상 '터진' 영상: {len(hits)}개 "
            f"({len(hits) / len(pool) * 100:.0f}%)")

    liked = [v for v in pool if v["likes"] and v["views"]]
    if liked:
        rate = sum(v["likes"] / v["views"] for v in liked) / len(liked)
        crate = sum(v["comments"] / v["views"] for v in liked) / len(liked)
        add(f"- 평균 좋아요율: {rate * 100:.2f}% / 댓글율: {crate * 100:.3f}%")

    add("")
    add("## 조회수 상위 10")
    add("")
    add("| 조회수 | 좋아요 | 길이 | 게시일 | 제목 |")
    add("|---:|---:|---:|---|---|")
    for v in sorted(pool, key=lambda x: x["views"], reverse=True)[:10]:
        title = v["title"].replace("|", "/")[:60]
        add(f"| {v['views']:,} | {v['likes'] or '-'} | {v['seconds']}s "
            f"| {v['published']:%Y-%m-%d} | [{title}](https://youtu.be/{v['id']}) |")

    add("")
    add("## 게시 요일 / 시간대 (KST)")
    add("")
    by_day = {}
    for v in pool:
        by_day.setdefault(v["published"].weekday(), []).append(v["views"])
    add("| 요일 | 개수 | 조회수 중앙값 |")
    add("|---|---:|---:|")
    for d in range(7):
        vs = by_day.get(d, [])
        add(f"| {WEEKDAYS[d]} | {len(vs)} | {int(pct(vs, 50)):,} |")

    hours = Counter(v["published"].hour for v in pool)
    if hours:
        top_h = ", ".join(f"{h}시({n})" for h, n in hours.most_common(4))
        add("")
        add(f"주로 올리는 시간대: {top_h}")

    add("")
    add("## 제목 키워드 상위")
    add("")
    kws = title_keywords(pool)
    add(", ".join(f"`{w}`({n})" for w, n in kws) if kws else "_없음_")

    lens = [len(v["title"]) for v in pool]
    if lens:
        add("")
        add(f"제목 평균 길이 {sum(lens) / len(lens):.0f}자 "
            f"(최단 {min(lens)} / 최장 {max(lens)})")

    tags = Counter(t for v in pool for t in v["tags"])
    if tags:
        add("")
        add("## 태그 상위")
        add("")
        add(", ".join(f"`{t}`({n})" for t, n in tags.most_common(15)))

    secs = [v["seconds"] for v in shorts]
    if secs:
        add("")
        add(f"쇼츠 길이: 중앙값 {int(pct(secs, 50))}초 "
            f"(최단 {min(secs)} / 최장 {max(secs)})")

    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="유튜브 채널 쇼츠 분석")
    ap.add_argument("channel", help="@handle, 채널 ID, 또는 채널 URL")
    ap.add_argument("--limit", type=int, default=200, help="분석할 최근 영상 수")
    ap.add_argument("--md", help="마크다운 리포트를 저장할 경로")
    args = ap.parse_args()

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit("YOUTUBE_API_KEY 환경변수가 필요합니다.")

    try:
        ch = resolve_channel(args.channel, key)
        uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]
        videos = fetch_videos(uploads, key, args.limit)
    except ApiError as e:
        sys.exit(f"API 오류: {e}")

    if not videos:
        sys.exit("공개된 영상이 없습니다.")

    report = build_report(ch, videos)
    print(report)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\n저장됨: {args.md}", file=sys.stderr)


if __name__ == "__main__":
    main()
