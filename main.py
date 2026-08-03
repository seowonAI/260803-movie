"""
어제의 박스오피스 순위 - Streamlit 앱
------------------------------------
- KOBIS(영화진흥위원회) 공식 오픈API를 사용합니다.
- 인증키는 절대 코드에 직접 쓰지 않고, Streamlit의 secrets(비밀 금고)에서 불러옵니다.
  (Streamlit Cloud > 앱 설정 > Secrets 메뉴에 KOBIS_KEY = "발급받은키" 형태로 등록하세요.)
- 조회 날짜는 '오늘'이 아니라 '어제'를 한국 시간(KST) 기준으로 자동 계산합니다.
  배포 서버의 시계는 한국 시간이 아닐 수 있기 때문에, 서버 시간대에 상관없이
  항상 한국 시간 기준으로 어제 날짜를 구하도록 처리했습니다.
"""

import streamlit as st
import pandas as pd
import requests
from datetime import datetime, timedelta

# zoneinfo는 파이썬 3.9 이상 표준 라이브러리입니다.
# 배포 환경(리눅스)에 시간대 데이터(tzdata)가 없을 수도 있어서 requirements.txt에 tzdata를 포함했습니다.
from zoneinfo import ZoneInfo

import plotly.express as px


# ------------------------------------------------------------------
# 1. 기본 설정
# ------------------------------------------------------------------
st.set_page_config(page_title="어제의 박스오피스", page_icon="🎬", layout="wide")

KOBIS_URL = "https://www.kobis.or.kr/kobisopenapi/webservice/rest/boxoffice/searchDailyBoxOfficeList.json"


def get_yesterday_kst() -> str:
    """
    한국 시간(KST) 기준으로 '어제' 날짜를 yyyymmdd 형식의 문자열로 반환합니다.
    서버가 어느 시간대에서 돌아가든, 항상 한국 시간을 기준으로 계산합니다.
    """
    now_kst = datetime.now(ZoneInfo("Asia/Seoul"))
    yesterday_kst = now_kst - timedelta(days=1)
    return yesterday_kst.strftime("%Y%m%d")


def format_date_for_display(yyyymmdd: str) -> str:
    """yyyymmdd 문자열을 'YYYY년 MM월 DD일' 형태로 보기 좋게 바꿔줍니다."""
    dt = datetime.strptime(yyyymmdd, "%Y%m%d")
    return dt.strftime("%Y년 %m월 %d일")


def format_open_date(yyyymmdd: str) -> str:
    """개봉일(yyyymmdd)을 'YYYY.MM.DD' 형태로 바꿔줍니다. 값이 없으면 그대로 반환합니다."""
    if not yyyymmdd or len(yyyymmdd) != 8:
        return yyyymmdd or "-"
    try:
        dt = datetime.strptime(yyyymmdd, "%Y%m%d")
        return dt.strftime("%Y.%m.%d")
    except ValueError:
        return yyyymmdd


# ------------------------------------------------------------------
# 2. KOBIS API 호출 함수
# ------------------------------------------------------------------
@st.cache_data(ttl=3600)  # 같은 날짜는 1시간 동안 캐시해서 API 호출을 아낍니다.
def fetch_box_office(target_dt: str):
    """
    KOBIS 일별 박스오피스 API를 호출합니다.
    반환값: (성공여부: bool, 결과: DataFrame 또는 오류메시지 문자열)
    """
    # secrets(비밀 금고)에 KOBIS_KEY가 없는 경우를 미리 확인합니다.
    if "KOBIS_KEY" not in st.secrets:
        return False, (
            "인증키(KOBIS_KEY)가 설정되어 있지 않습니다. "
            "Streamlit Cloud의 앱 설정 > Secrets 메뉴에서 "
            "KOBIS_KEY 값을 등록했는지 확인해 주세요."
        )

    api_key = st.secrets["KOBIS_KEY"]
    params = {"key": api_key, "targetDt": target_dt}

    # 2-1. 요청 자체가 실패하는 경우 (네트워크 오류, 타임아웃 등)
    try:
        response = requests.get(KOBIS_URL, params=params, timeout=10)
    except requests.exceptions.RequestException as e:
        return False, (
            "박스오피스 정보를 가져오는 중 네트워크 오류가 발생했습니다. "
            "인터넷 연결 상태나 KOBIS 서버 상태를 확인해 주세요.\n\n"
            f"(상세 오류: {e})"
        )

    # 2-2. 응답이 200이 아닌 경우 (서버 오류 등)
    if response.status_code != 200:
        return False, (
            f"KOBIS 서버가 오류 상태코드({response.status_code})를 반환했습니다. "
            "잠시 후 다시 시도하거나 KOBIS 서버 상태를 확인해 주세요."
        )

    # 2-3. 응답이 JSON 형식이 아닌 경우
    try:
        data = response.json()
    except ValueError:
        return False, (
            "KOBIS 서버로부터 받은 응답이 올바른 JSON 형식이 아닙니다. "
            "요청 주소나 파라미터가 정확한지 확인해 주세요."
        )

    # 2-4. 인증키가 틀린 경우: 상태코드는 200이지만 faultInfo 상자가 들어옵니다.
    if "faultInfo" in data:
        fault = data["faultInfo"]
        message = fault.get("message", "알 수 없는 오류")
        return False, (
            "KOBIS API가 오류를 반환했습니다. 인증키(KOBIS_KEY)가 정확한지, "
            "그리고 발급받은 키가 유효한 상태인지 확인해 주세요.\n\n"
            f"(KOBIS 오류 메시지: {message})"
        )

    # 2-5. 예상한 구조(boxOfficeResult)가 없는 경우
    if "boxOfficeResult" not in data:
        return False, (
            "응답 형식이 예상과 다릅니다. KOBIS API 문서가 변경되었을 수 있으니 "
            "요청 주소와 응답 구조를 다시 확인해 주세요."
        )

    movie_list = data["boxOfficeResult"].get("dailyBoxOfficeList", [])

    # 2-6. 영화 목록이 비어 있는 경우 (해당 날짜에 집계된 데이터가 없음)
    if not movie_list:
        return False, (
            "해당 날짜의 박스오피스 데이터가 비어 있습니다. "
            "아직 집계가 완료되지 않았거나, 조회 날짜(targetDt)가 잘못되었을 수 있습니다."
        )

    # 2-7. 정상적으로 데이터를 받은 경우: DataFrame으로 변환
    df = pd.DataFrame(movie_list)

    # 문서에 나온 대로, 숫자 값들이 전부 문자열로 오기 때문에 숫자형으로 바꿔줍니다.
    numeric_cols = ["rank", "rankInten", "audiCnt", "audiAcc", "scrnCnt", "showCnt"]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return True, df


# ------------------------------------------------------------------
# 3. 화면 구성
# ------------------------------------------------------------------
st.title("🎬 어제의 박스오피스")

target_dt = get_yesterday_kst()
st.caption(f"조회 기준일 (한국 시간 기준 어제): {format_date_for_display(target_dt)}")

success, result = fetch_box_office(target_dt)

if not success:
    # 실패/오류/빈 목록 - 빈 화면 대신 무엇을 확인해야 하는지 안내합니다.
    st.error(f"⚠️ 박스오피스 정보를 불러오지 못했습니다.\n\n{result}")
    st.info(
        "확인해 보세요:\n"
        "1. Streamlit Cloud > 앱 설정 > Secrets 에 KOBIS_KEY가 올바르게 등록되어 있는지\n"
        "2. KOBIS 인증키가 유효한(만료되지 않은) 상태인지\n"
        "3. 조회하려는 날짜에 실제로 박스오피스 데이터가 존재하는지 (예: 서비스 시작 전 날짜는 아닌지)\n"
        "4. 네트워크/방화벽 설정으로 인해 kobis.or.kr에 접속이 막혀 있지는 않은지"
    )
    st.stop()

df = result

# 순위 기준으로 정렬해서 보여줍니다.
df = df.sort_values("rank").reset_index(drop=True)

# ------------------------------------------------------------------
# 3-1. 1위 영화 - 지표 카드 세 장
# ------------------------------------------------------------------
top_movie = df.iloc[0]

st.subheader(f"🥇 1위: {top_movie['movieNm']}")

col1, col2, col3 = st.columns(3)
col1.metric("어제 관객수", f"{int(top_movie['audiCnt']):,} 명")
col2.metric("누적 관객수", f"{int(top_movie['audiAcc']):,} 명")
col3.metric("스크린수", f"{int(top_movie['scrnCnt']):,} 개")

st.divider()

# ------------------------------------------------------------------
# 3-2. 관객수 상위 5편 - 막대그래프
# ------------------------------------------------------------------
st.subheader("📊 관객수 상위 5편")

top5 = df.sort_values("audiCnt", ascending=False).head(5)

fig = px.bar(
    top5,
    x="movieNm",
    y="audiCnt",
    text="audiCnt",
    labels={"movieNm": "영화명", "audiCnt": "관객수(명)"},
)
fig.update_traces(texttemplate="%{text:,}", textposition="outside")
fig.update_layout(yaxis_title="관객수(명)", xaxis_title="")

st.plotly_chart(fig, use_container_width=True)

st.divider()

# ------------------------------------------------------------------
# 3-3. 전체 표 - 순위 · 영화명 · 개봉일 · 관객수 · 누적관객 · 스크린수
# ------------------------------------------------------------------
st.subheader("📋 전체 박스오피스 순위")

table_df = df[["rank", "movieNm", "openDt", "audiCnt", "audiAcc", "scrnCnt"]].copy()
table_df["openDt"] = table_df["openDt"].apply(format_open_date)

table_df = table_df.rename(columns={
    "rank": "순위",
    "movieNm": "영화명",
    "openDt": "개봉일",
    "audiCnt": "관객수",
    "audiAcc": "누적관객",
    "scrnCnt": "스크린수",
})

st.dataframe(
    table_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "관객수": st.column_config.NumberColumn(format="%d"),
        "누적관객": st.column_config.NumberColumn(format="%d"),
        "스크린수": st.column_config.NumberColumn(format="%d"),
    },
)
