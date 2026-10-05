# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import gspread
from google.oauth2.service_account import Credentials

# ---------- 連接 Google Sheets ----------
@st.cache_resource
def get_gsheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet(name):
    client = get_gsheet_client()
    sheet_id = st.secrets["gcp"]["sheet_id"]
    return client.open_by_key(sheet_id).worksheet(name)

@st.cache_data(ttl=5)
def load_data(sheet_name, columns=None):
    try:
        ws = get_sheet(sheet_name)
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame(columns=columns) if columns else pd.DataFrame()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"讀取 {sheet_name} 失敗：{e}")
        return pd.DataFrame(columns=columns) if columns else pd.DataFrame()

def save_data(df, sheet_name):
    try:
        ws = get_sheet(sheet_name)
        ws.clear()
        ws.update([df.columns.tolist()] + df.values.tolist())
        st.cache_data.clear()
    except Exception as e:
        st.error(f"寫入 {sheet_name} 失敗：{e}")

# ---------- 設定 ----------
st.set_page_config(page_title="家教時間管理", page_icon="📚", layout="wide")

STUDENT_COLS = ["id", "name", "subject", "hourly_rate", "note"]
LESSON_COLS = ["id", "student_id", "date", "start", "end", "type", "status", "note"]
REQ_COLS = ["id", "student_id", "type", "original_lesson_id", "requested_date",
            "requested_start", "requested_end", "reason", "status", "created_at", "teacher_note"]

students = load_data("students", STUDENT_COLS)
lessons = load_data("lessons", LESSON_COLS)
requests_df = load_data("requests", REQ_COLS)

# 型別修正
if not students.empty:
    students["id"] = pd.to_numeric(students["id"], errors="coerce").fillna(0).astype(int)
    students["hourly_rate"] = pd.to_numeric(students["hourly_rate"], errors="coerce").fillna(0).astype(int)

if not lessons.empty:
    lessons["id"] = pd.to_numeric(lessons["id"], errors="coerce").fillna(0).astype(int)
    lessons["student_id"] = pd.to_numeric(lessons["student_id"], errors="coerce").fillna(0).astype(int)

# ---------- 側邊欄 ----------
st.sidebar.title("📚 家教管理系統")
menu = st.sidebar.radio("功能選單", [
    "🏠 首頁總覽",
    "👤 學生管理",
    "📅 課程排程",
    "🔄 補課管理",
    "📋 申請審核",
    "📊 時數統計",
])

# ---------- 首頁 ----------
if menu == "🏠 首頁總覽":
    st.title("🏠 首頁總覽")
    today = date.today()
    upcoming = lessons.copy()
    if not upcoming.empty:
        upcoming["date"] = pd.to_datetime(upcoming["date"], errors="coerce").dt.date
        upcoming = upcoming[upcoming["date"] >= today].sort_values(["date", "start"])

    pending_requests = len(requests_df[requests_df["status"] == "待處理"]) if not requests_df.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("學生總數", len(students))
    col2.metric("本週課程", len(upcoming[upcoming["date"] <= today + timedelta(days=7)]) if not upcoming.empty else 0)
    col3.metric("待補課", len(lessons[lessons["status"] == "待補課"]) if not lessons.empty else 0)
    col4.metric("待審核", pending_requests)

    if pending_requests > 0:
        st.warning(f"⚠️ 有 {pending_requests} 筆申請待審核，請到「📋 申請審核」處理")

    st.subheader("📌 即將到來的課程")
    if upcoming.empty:
        st.info("目前沒有安排課程")
    else:
        show = upcoming.head(10).copy()
        name_map = students.set_index("id")["name"].to_dict() if not students.empty else {}
        show["學生"] = show["student_id"].map(name_map)
        st.dataframe(show[["date", "start", "end", "學生", "type", "status"]], use_container_width=True)

# ---------- 學生管理 ----------
elif menu == "👤 學生管理":
    st.title("👤 學生管理")

    with st.expander("➕ 新增學生", expanded=False):
        with st.form("add_student"):
            name = st.text_input("姓名")
            subject = st.text_input("科目")
            rate = st.number_input("時薪", min_value=0, step=50)
            note = st.text_area("備註")
            if st.form_submit_button("新增"):
                if name.strip() == "":
                    st.warning("姓名不能為空")
                else:
                    new_id = int(students["id"].max() + 1) if not students.empty else 1
                    new_row = pd.DataFrame([[new_id, name, subject, rate, note]], columns=STUDENT_COLS)
                    students = pd.concat([students, new_row], ignore_index=True)
                    save_data(students, "students")
                    st.success(f"已新增學生 {name}")
                    st.rerun()

    st.subheader("學生列表")
    if students.empty:
        st.info("尚無學生資料")
    else:
        st.dataframe(students, use_container_width=True)
        del_id = st.selectbox("刪除學生 ID", students["id"])
        if st.button("🗑️ 刪除"):
            students = students[students["id"] != del_id]
            save_data(students, "students")
            st.rerun()

# ---------- 課程排程 ----------
elif menu == "📅 課程排程":
    st.title("📅 課程排程")

    if students.empty:
        st.warning("請先新增學生")
    else:
        with st.form("add_lesson"):
            student = st.selectbox("學生", students["name"])
            sid = int(students[students["name"] == student]["id"].values[0])
            d = st.date_input("日期", value=date.today())
            c1, c2 = st.columns(2)
            start = c1.time_input("開始時間", value=datetime.strptime("19:00", "%H:%M").time())
            end = c2.time_input("結束時間", value=datetime.strptime("20:00", "%H:%M").time())
            ltype = st.selectbox("類型", ["正課", "補課", "試聽"])
            note = st.text_input("備註")
            if st.form_submit_button("新增課程"):
                new_id = int(lessons["id"].max() + 1) if not lessons.empty else 1
                new_row = pd.DataFrame(
                    [[new_id, sid, str(d), start.strftime("%H:%M"), end.strftime("%H:%M"), ltype, "已排定", note]],
                    columns=LESSON_COLS
                )
                lessons = pd.concat([lessons, new_row], ignore_index=True)
                save_data(lessons, "lessons")
                st.success("課程已新增")
                st.rerun()

        st.subheader("所有課程")
        if lessons.empty:
            st.info("尚無課程")
        else:
            view = lessons.copy()
            name_map = students.set_index("id")["name"].to_dict()
            view["學生"] = view["student_id"].map(name_map)
            st.dataframe(view[["id", "date", "start", "end", "學生", "type", "status", "note"]], use_container_width=True)

# ---------- 補課管理 ----------
elif menu == "🔄 補課管理":
    st.title("🔄 補課管理")

    st.subheader("標記請假課程")
    if lessons.empty:
        st.info("尚無課程可操作")
    else:
        pending = lessons[lessons["status"] == "已排定"]
        if not pending.empty:
            lid = st.selectbox("選擇要請假的課程", pending["id"])
            if st.button("標記為待補課"):
                lessons.loc[lessons["id"] == lid, "status"] = "待補課"
                save_data(lessons, "lessons")
                st.success("已標記，請安排補課時間")
                st.rerun()

    st.subheader("待補課清單")
    todo = lessons[lessons["status"] == "待補課"]
    if todo.empty:
        st.success("沒有待補課項目 🎉")
    else:
        todo_view = todo.copy()
        name_map = students.set_index("id")["name"].to_dict()
        todo_view["學生"] = todo_view["student_id"].map(name_map)
        st.dataframe(todo_view[["id", "date", "start", "end", "學生", "note"]], use_container_width=True)

        st.markdown("### 安排補課")
        with st.form("makeup"):
            lid = st.selectbox("補課課程 ID", todo["id"])
            new_date = st.date_input("補課日期")
            c1, c2 = st.columns(2)
            ns = c1.time_input("開始", value=datetime.strptime("19:00", "%H:%M").time())
            ne = c2.time_input("結束", value=datetime.strptime("20:00", "%H:%M").time())
            if st.form_submit_button("建立補課"):
                sid = int(lessons[lessons["id"] == lid]["student_id"].values[0])
                new_id = int(lessons["id"].max() + 1)
                new_row = pd.DataFrame(
                    [[new_id, sid, str(new_date), ns.strftime("%H:%M"), ne.strftime("%H:%M"), "補課", "已排定", f"補原課程 {lid}"]],
                    columns=LESSON_COLS
                )
                lessons = pd.concat([lessons, new_row], ignore_index=True)
                lessons.loc[lessons["id"] == lid, "status"] = "已補課"
                save_data(lessons, "lessons")
                st.success("補課已建立")
                st.rerun()

# ---------- 申請審核 ----------
elif menu == "📋 申請審核":
    st.title("📋 申請審核")

    if requests_df.empty:
        st.info("目前沒有申請")
    else:
        # 待處理
        pending = requests_df[requests_df["status"] == "待處理"]

        if pending.empty:
            st.success("🎉 沒有待處理的申請")

        # 歷史紀錄
        st.subheader(f"⏳ 待處理申請（{len(pending)} 筆）")

        name_map = students.set_index("id")["name"].to_dict() if not students.empty else {}
        lesson_map = lessons.set_index("id").to_dict("index") if not lessons.empty else {}

        for idx, row in pending.iterrows():
            req_id = row["id"]
            sid = row["student_id"]
            student_name = name_map.get(int(sid) if str(sid).isdigit() else sid, f"學生 {sid}")

            with st.container():
                st.markdown(f"### 📝 {row['type']} — {student_name}")

                col1, col2 = st.columns([2, 1])
                with col1:
                    st.markdown(f"**原因：** {row.get('reason', '（無）')}")
                    st.markdown(f"**送出時間：** {row.get('created_at', '')}")

                    # 如果是請假，顯示原課程資訊
                    if row["type"] == "請假":
                        orig_id = row.get("original_lesson_id")
                        if str(orig_id).isdigit() and int(orig_id) in lesson_map:
                            orig = lesson_map[int(orig_id)]
                            st.info(f"📅 原課程：{orig.get('date')} {orig.get('start')}-{orig.get('end')}（{orig.get('type')}）")
                        else:
                            st.caption(f"原課程 ID：{orig_id}")

                with col2:
                    teacher_note = st.text_input("老師備註", key=f"note_{req_id}")

                c1, c2, c3 = st.columns([1, 1, 3])
                with c1:
                    if st.button("✅ 同意", key=f"approve_{req_id}", use_container_width=True):
                        requests_df.loc[requests_df["id"] == req_id, "status"] = "已同意"
                        requests_df.loc[requests_df["id"] == req_id, "teacher_note"] = teacher_note

                        # 如果是請假，原課程標記為待補課
                        if row["type"] == "請假":
                            orig_id = row.get("original_lesson_id")
                            if str(orig_id).isdigit():
                                lessons.loc[lessons["id"] == int(orig_id), "status"] = "待補課"
                                save_data(lessons, "lessons")

                        save_data(requests_df, "requests")
                        st.success("已同意")
                        st.rerun()

                with c2:
                    if st.button("❌ 拒絕", key=f"reject_{req_id}", use_container_width=True):
                        requests_df.loc[requests_df["id"] == req_id, "status"] = "已拒絕"
                        requests_df.loc[requests_df["id"] == req_id, "teacher_note"] = teacher_note
                        save_data(requests_df, "requests")
                        st.success("已拒絕")
                        st.rerun()

                st.markdown("---")

        # 歷史紀錄
        with st.expander("📜 查看歷史紀錄"):
            history = requests_df[requests_df["status"] != "待處理"].copy()
            if history.empty:
                st.info("沒有歷史紀錄")
            else:
                history["學生"] = history["student_id"].apply(
                    lambda x: name_map.get(int(x) if str(x).isdigit() else x, f"學生 {x}")
                )
                st.dataframe(
                    history[["id", "學生", "type", "status", "created_at", "teacher_note"]],
                    use_container_width=True
                )

# ---------- 時數統計 ----------
elif menu == "📊 時數統計":
    st.title("📊 時數統計")

    if lessons.empty or students.empty:
        st.info("資料不足")
    else:
        df = lessons.copy()
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df["start_dt"] = pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + df["start"], errors="coerce")
        df["end_dt"] = pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + df["end"], errors="coerce")
        df["hours"] = (df["end_dt"] - df["start_dt"]).dt.total_seconds() / 3600
        name_map = students.set_index("id")["name"].to_dict()
        df["學生"] = df["student_id"].map(name_map)

        months = sorted(df["date"].dt.strftime("%Y-%m").dropna().unique(), reverse=True)
        month = st.selectbox("選擇月份", ["全部"] + list(months))
        if month != "全部":
            df = df[df["date"].dt.strftime("%Y-%m") == month]

        summary = df.groupby("學生")["hours"].sum().reset_index()
        summary.columns = ["學生", "總時數"]

        rate_map = students.set_index("name")["hourly_rate"].to_dict()
        summary["預估收入"] = summary.apply(lambda r: r["總時數"] * rate_map.get(r["學生"], 0), axis=1)
        st.metric("總收入", f"NT$ {int(summary['預估收入'].sum()):,}")
        st.dataframe(summary, use_container_width=True)

        import plotly.express as px
        fig = px.bar(summary, x="學生", y="總時數", title="各學生上課時數")
        st.plotly_chart(fig, use_container_width=True)
