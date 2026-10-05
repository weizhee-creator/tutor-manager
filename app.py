# -*- coding: utf-8 -*-
import os
import time
os.environ["TZ"] = "Asia/Taipei"
try:
    time.tzset()
except AttributeError:
    pass

import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import gspread
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

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

@st.cache_data(ttl=300)
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

# ---------- Google Drive（OAuth） ----------
@st.cache_resource
def get_drive_service():
    client_id = st.secrets["oauth"]["client_id"]
    client_secret = st.secrets["oauth"]["client_secret"]
    refresh_token = st.secrets["gcp"]["refresh_token"]

    creds = OAuthCredentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)

def parse_date_safe(series):
    result = pd.to_datetime(series, errors="coerce", format="%Y-%m-%d")
    mask = result.isna()
    if mask.any():
        result2 = pd.to_datetime(series[mask], errors="coerce", format="%Y/%m/%d")
        result.loc[mask] = result2
    return result

def safe_next_id(df, id_col="id"):
    if df is None or df.empty or id_col not in df.columns:
        return 1
    ids = pd.to_numeric(df[id_col], errors="coerce").dropna()
    if ids.empty:
        return 1
    return int(ids.max() + 1)

# ---------- Google Drive 工具 ----------
def find_or_create_folder(service, name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    metadata = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]

def list_files(service, folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, size, createdTime, webViewLink)",
        orderBy="createdTime desc"
    ).execute()
    return results.get("files", [])

def upload_file(service, folder_id, filename, file_bytes, mime_type):
    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=False)
    file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return file["id"]

def delete_file(service, file_id):
    service.files().delete(fileId=file_id).execute()

def get_student_folder(service, student):
    root_id = st.secrets["gcp"]["drive_folder_id"]
    student_folder_name = f"S{student['id']:03d}_{student['name']}"
    return find_or_create_folder(service, student_folder_name, root_id)

# ---------- 設定 ----------
st.set_page_config(page_title="家教時間管理", page_icon="📚", layout="wide")

STUDENT_COLS = ["id", "name", "subject", "hourly_rate", "note"]
LESSON_COLS = ["id", "student_id", "date", "start", "end", "type", "status", "note"]
REQ_COLS = ["id", "student_id", "type", "original_lesson_id", "requested_date",
            "requested_start", "requested_end", "reason", "status", "created_at", "teacher_note"]
PROG_COLS = ["id", "student_id", "lesson_id", "date", "content", "homework", "note", "created_at"]

students = load_data("students", STUDENT_COLS)
lessons = load_data("lessons", LESSON_COLS)
requests_df = load_data("requests", REQ_COLS)
progress_df = load_data("progress", PROG_COLS)

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
    "📆 週期排課",
    "🔄 補課管理",
    "📋 申請審核",
    "📊 上課進度",
    "📁 檔案管理",
    "📈 時數統計",
])

# ---------- 首頁 ----------
if menu == "🏠 首頁總覽":
    st.title("🏠 首頁總覽")
    today = date.today()
    upcoming = lessons.copy()
    if not upcoming.empty:
        upcoming["date"] = parse_date_safe(upcoming["date"]).dt.date
        upcoming = upcoming[
            (upcoming["date"] >= today) &
            (upcoming["status"].astype(str) != "已補課")
        ].sort_values(["date", "start"])

    pending_requests = len(requests_df[requests_df["status"] == "待處理"]) if not requests_df.empty else 0

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("學生總數", len(students))
    col2.metric("本週課程", len(upcoming[upcoming["date"] <= today + timedelta(days=7)]) if not upcoming.empty else 0)
    col3.metric("待補課", len(lessons[lessons["status"] == "待補課"]) if not lessons.empty else 0)
    col4.metric("待審核", pending_requests)

    if pending_requests > 0:
        st.warning(f"⚠️ 有 {pending_requests} 筆申請待審核")

    st.subheader("📌 即將到來的課程")
    if upcoming.empty:
        st.info("目前沒有安排課程")
    else:
        show = upcoming.head(20).copy()
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
                    new_id = safe_next_id(students)
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
        # ---------- 新增課程 ----------
        with st.expander("➕ 新增課程", expanded=False):
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
                    new_id = safe_next_id(lessons)
                    new_row = pd.DataFrame(
                        [[new_id, sid, str(d), start.strftime("%H:%M"), end.strftime("%H:%M"), ltype, "已排定", note]],
                        columns=LESSON_COLS
                    )
                    lessons = pd.concat([lessons, new_row], ignore_index=True)
                    save_data(lessons, "lessons")
                    st.success("課程已新增")
                    st.rerun()

        st.markdown("---")

        # ---------- 所有課程（含篩選） ----------
        st.subheader("📋 所有課程")

        col_f1, col_f2 = st.columns(2)
        with col_f1:
            filter_student = st.selectbox("篩選學生", ["全部"] + list(students["name"]))
        with col_f2:
            if not lessons.empty and "date" in lessons.columns:
                lessons_dates = parse_date_safe(lessons["date"]).dropna()
                months = sorted(lessons_dates.dt.strftime("%Y-%m").unique(), reverse=True)
            else:
                months = []
            filter_month = st.selectbox("篩選月份", ["全部"] + list(months))

        view = lessons.copy()
        name_map = students.set_index("id")["name"].to_dict()
        view["學生"] = view["student_id"].map(name_map)

        if filter_student != "全部":
            view = view[view["學生"] == filter_student]

        if filter_month != "全部" and not view.empty:
            view["_date_parsed"] = parse_date_safe(view["date"])
            view = view[view["_date_parsed"].dt.strftime("%Y-%m") == filter_month]
            view = view.drop(columns=["_date_parsed"])

        if view.empty:
            st.info("沒有符合的課程")
        else:
            st.dataframe(view[["id", "date", "start", "end", "學生", "type", "status", "note"]], use_container_width=True)

        st.markdown("---")

        # ---------- 刪除課程 ----------
        st.subheader("🗑️ 刪除課程")
        st.caption("刪除時會一併刪除相關的請假申請與進度紀錄")

        if view.empty:
            st.info("沒有課程可刪除")
        else:
            del_options = {}
            for _, row in view.iterrows():
                label = f"[{row['id']}] {row.get('date', '')} {row.get('start', '')}-{row.get('end', '')} {row.get('學生', '')} ({row.get('type', '')})"
                del_options[label] = int(row["id"])

            selected_del = st.selectbox("選擇要刪除的課程", list(del_options.keys()))
            del_id = del_options[selected_del]

            st.warning(f"⚠️ 將刪除課程 ID = {del_id}，及其相關的請假申請與進度紀錄")

            confirm = st.checkbox("我確認要刪除這堂課（無法復原）", key="confirm_del_lesson")

            if st.button("🗑️ 確定刪除", disabled=not confirm, type="primary"):
                # 1. 刪除課程
                lessons = lessons[lessons["id"] != del_id]
                save_data(lessons, "lessons")

                # 2. 刪除相關的 requests（original_lesson_id == del_id）
                if not requests_df.empty and "original_lesson_id" in requests_df.columns:
                    before = len(requests_df)
                    requests_df = requests_df[
                        pd.to_numeric(requests_df["original_lesson_id"], errors="coerce") != del_id
                    ]
                    after = len(requests_df)
                    if before != after:
                        save_data(requests_df, "requests")

                # 3. 刪除相關的 progress（lesson_id == del_id）
                if not progress_df.empty and "lesson_id" in progress_df.columns:
                    before = len(progress_df)
                    progress_df = progress_df[
                        pd.to_numeric(progress_df["lesson_id"], errors="coerce") != del_id
                    ]
                    after = len(progress_df)
                    if before != after:
                        save_data(progress_df, "progress")

                st.success(f"✅ 已刪除課程 ID = {del_id}")
                st.rerun()

# ---------- 週期排課 ----------
elif menu == "📆 週期排課":
    st.title("📆 週期排課")
    st.caption("一次產生多堂固定課程")

    if students.empty:
        st.warning("請先新增學生")
    else:
        col1, col2 = st.columns(2)
        with col1:
            student = st.selectbox("選擇學生", students["name"])
            sid = int(students[students["name"] == student]["id"].values[0])
            st.markdown("**上課星期（可多選）**")
            weekdays_cn = ["一", "二", "三", "四", "五", "六", "日"]
            selected_weekdays = []
            cols = st.columns(7)
            for i, wd in enumerate(weekdays_cn):
                with cols[i]:
                    if st.checkbox(wd, key=f"wd_{i}"):
                        selected_weekdays.append(i)

        with col2:
            st.markdown("**上課時間**")
            c1, c2 = st.columns(2)
            start = c1.time_input("開始", value=datetime.strptime("19:00", "%H:%M").time(), key="period_start")
            end = c2.time_input("結束", value=datetime.strptime("20:00", "%H:%M").time(), key="period_end")
            st.markdown("**產生範圍**")
            quick = st.radio("快速選", ["1 個月", "2 個月", "自訂"], horizontal=True)
            today = date.today()
            if quick == "1 個月":
                start_date = st.date_input("從", value=today)
                end_date = st.date_input("到", value=today + timedelta(days=30))
            elif quick == "2 個月":
                start_date = st.date_input("從", value=today)
                end_date = st.date_input("到", value=today + timedelta(days=60))
            else:
                start_date = st.date_input("從", value=today)
                end_date = st.date_input("到", value=today + timedelta(days=30))

        st.markdown("---")

        if not selected_weekdays:
            st.info("請至少勾選一個上課星期")
        else:
            preview_dates = []
            cur = start_date
            while cur <= end_date:
                if cur.weekday() in selected_weekdays:
                    preview_dates.append(cur)
                cur += timedelta(days=1)

            st.subheader(f"📋 預覽：將產生 {len(preview_dates)} 堂課")
            if len(preview_dates) == 0:
                st.warning("此範圍內沒有符合的日期")
            else:
                preview_df = pd.DataFrame({
                    "日期": [d.strftime("%Y-%m-%d") for d in preview_dates],
                    "星期": [['一', '二', '三', '四', '五', '六', '日'][d.weekday()] for d in preview_dates],
                    "開始": [start.strftime("%H:%M")] * len(preview_dates),
                    "結束": [end.strftime("%H:%M")] * len(preview_dates),
                })
                st.dataframe(preview_df, use_container_width=True, height=300)

                if st.button("✅ 批次建立", type="primary"):
                    new_lessons = lessons.copy() if not lessons.empty else pd.DataFrame(columns=LESSON_COLS)
                    next_id = safe_next_id(new_lessons)
                    rows = []
                    for d in preview_dates:
                        rows.append([next_id, sid, str(d), start.strftime("%H:%M"), end.strftime("%H:%M"), "正課", "已排定", ""])
                        next_id += 1
                    new_rows_df = pd.DataFrame(rows, columns=LESSON_COLS)
                    new_lessons = pd.concat([new_lessons, new_rows_df], ignore_index=True)
                    save_data(new_lessons, "lessons")
                    st.success(f"✅ 已建立 {len(rows)} 堂課！")
                    st.balloons()

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
                st.success("已標記")
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
                new_id = safe_next_id(lessons)
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
        pending = requests_df[requests_df["status"] == "待處理"]
        if pending.empty:
            st.success("🎉 沒有待處理的申請")

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
                    if row["type"] == "請假":
                        orig_id = row.get("original_lesson_id")
                        if str(orig_id).isdigit() and int(orig_id) in lesson_map:
                            orig = lesson_map[int(orig_id)]
                            st.info(f"📅 原課程：{orig.get('date')} {orig.get('start')}-{orig.get('end')}（{orig.get('type')}）")
                with col2:
                    teacher_note = st.text_input("老師備註", key=f"note_{req_id}")

                c1, c2, c3 = st.columns([1, 1, 3])
                with c1:
                    if st.button("✅ 同意", key=f"approve_{req_id}", use_container_width=True):
                        requests_df.loc[requests_df["id"] == req_id, "status"] = "已同意"
                        requests_df.loc[requests_df["id"] == req_id, "teacher_note"] = teacher_note
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

# ---------- 上課進度 ----------
elif menu == "📊 上課進度":
    st.title("📊 上課進度紀錄")

    if students.empty:
        st.warning("請先新增學生")
    else:
        col1, col2 = st.columns(2)
        with col1:
            student = st.selectbox("選擇學生", students["name"])
            sid = int(students[students["name"] == student]["id"].values[0])

        my_lessons = lessons[lessons["student_id"] == sid].copy() if not lessons.empty else pd.DataFrame()

        if my_lessons.empty:
            st.info("該學生尚無課程")
        else:
            my_lessons["date_parsed"] = parse_date_safe(my_lessons["date"])
            my_lessons = my_lessons.sort_values("date_parsed", ascending=False)
            today = pd.Timestamp(date.today())
            recordable = my_lessons[my_lessons["date_parsed"] <= today]

            if recordable.empty:
                st.info("尚無已發生的課程可記錄")
            else:
                options = {}
                for _, row in recordable.iterrows():
                    d_str = row["date_parsed"].strftime("%Y-%m-%d") if pd.notna(row["date_parsed"]) else str(row["date"])
                    label = f"{d_str} {row['start']}-{row['end']}（{row.get('type', '')}）"
                    options[label] = int(row["id"])

                selected_label = st.selectbox("選擇課程", list(options.keys()))
                lesson_id = options[selected_label]
                existing = progress_df[progress_df["lesson_id"].astype(str) == str(lesson_id)] if not progress_df.empty else pd.DataFrame()

                if not existing.empty:
                    ex = existing.iloc[0]
                    default_content = str(ex.get("content", ""))
                    default_homework = str(ex.get("homework", ""))
                    default_note = str(ex.get("note", ""))
                    st.info("📝 這堂課已有紀錄，修改後會覆蓋")
                else:
                    default_content = ""
                    default_homework = ""
                    default_note = ""

                with st.form("progress_form"):
                    content = st.text_area("上課內容 / 進度", value=default_content, height=150)
                    homework = st.text_area("作業", value=default_homework, height=100)
                    note = st.text_area("備註", value=default_note, height=80)

                    if st.form_submit_button("💾 儲存進度", use_container_width=True):
                        if not content.strip():
                            st.error("請填寫上課內容")
                        else:
                            lesson_row = recordable[recordable["id"] == lesson_id].iloc[0]
                            d_str = lesson_row["date_parsed"].strftime("%Y-%m-%d") if pd.notna(lesson_row["date_parsed"]) else str(lesson_row["date"])
                            if not existing.empty:
                                progress_df.loc[progress_df["lesson_id"].astype(str) == str(lesson_id), "content"] = content
                                progress_df.loc[progress_df["lesson_id"].astype(str) == str(lesson_id), "homework"] = homework
                                progress_df.loc[progress_df["lesson_id"].astype(str) == str(lesson_id), "note"] = note
                            else:
                                new_id = safe_next_id(progress_df)
                                new_row = pd.DataFrame([[
                                    new_id, sid, lesson_id, d_str, content, homework, note,
                                    datetime.now().strftime("%Y-%m-%d %H:%M")
                                ]], columns=PROG_COLS)
                                progress_df = pd.concat([progress_df, new_row], ignore_index=True)
                            save_data(progress_df, "progress")
                            st.success("✅ 進度已儲存")
                            st.rerun()

        st.markdown("---")
        st.subheader("📚 已記錄的進度")
        my_progress = progress_df[progress_df["student_id"].astype(str) == str(sid)] if not progress_df.empty else pd.DataFrame()

        if my_progress.empty:
            st.info("尚無進度紀錄")
        else:
            my_progress = my_progress.copy()
            my_progress["date_parsed"] = parse_date_safe(my_progress["date"])
            my_progress = my_progress.sort_values("date_parsed", ascending=False)
            for _, row in my_progress.iterrows():
                with st.container():
                    st.markdown(f"### 📅 {row.get('date', '')}")
                    st.markdown(f"**📖 上課內容：**")
                    st.markdown(f"{row.get('content', '')}")
                    if row.get("homework"):
                        st.markdown(f"**📝 作業：** {row['homework']}")
                    if row.get("note"):
                        st.caption(f"💬 {row['note']}")
                    st.caption(f"記錄時間：{row.get('created_at', '')}")
                    st.markdown("---")

# ---------- 檔案管理 ----------
elif menu == "📁 檔案管理":
    st.title("📁 檔案管理")
    st.caption("為每位學生建立專屬資料夾，上傳教材、講義、筆記")

    if students.empty:
        st.warning("請先新增學生")
    else:
        student = st.selectbox("選擇學生", students["name"])
        sid = int(students[students["name"] == student]["id"].values[0])
        student_row = students[students["id"] == sid].iloc[0]

        try:
            service = get_drive_service()
            folder_id = get_student_folder(service, student_row)
            st.success(f"📁 學生資料夾：S{sid:03d}_{student}")

            st.markdown("---")
            st.subheader("⬆️ 上傳檔案")
            uploaded_file = st.file_uploader("選擇檔案", key="teacher_upload")
            col1, col2 = st.columns(2)
            with col1:
                subject_folder = st.selectbox("分類", ["教材", "筆記", "作業", "講義", "其他"], key="upload_category")
            with col2:
                if uploaded_file is not None:
                    st.caption(f"檔案大小：{uploaded_file.size / 1024:.1f} KB")

            if uploaded_file is not None:
                if st.button("📤 上傳", type="primary"):
                    try:
                        sub_folder_id = find_or_create_folder(service, subject_folder, folder_id)
                        upload_file(service, sub_folder_id, uploaded_file.name,
                                    uploaded_file.getvalue(),
                                    uploaded_file.type or "application/octet-stream")
                        st.success(f"✅ 已上傳：{uploaded_file.name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"上傳失敗：{e}")

            st.markdown("---")
            st.subheader("📂 已上傳的檔案")

            subfolders = service.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)"
            ).execute().get("files", [])

            if not subfolders:
                st.info("尚未建立任何分類資料夾")
            else:
                for subfolder in subfolders:
                    with st.expander(f"📁 {subfolder['name']}", expanded=False):
                        files = list_files(service, subfolder["id"])
                        if not files:
                            st.caption("（空）")
                        else:
                            for f in files:
                                c1, c2, c3 = st.columns([4, 1, 1])
                                with c1:
                                    size_kb = int(f.get("size", 0)) / 1024 if f.get("size") else 0
                                    st.markdown(f"📄 **{f['name']}** （{size_kb:.1f} KB）")
                                with c2:
                                    st.markdown(f"[🔗 開啟]({f.get('webViewLink', '#')})")
                                with c3:
                                    if st.button("🗑️", key=f"del_{f['id']}"):
                                        try:
                                            delete_file(service, f["id"])
                                            st.success("已刪除")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"刪除失敗：{e}")
        except Exception as e:
            st.error(f"Drive 連線失敗：{e}")

# ---------- 時數統計 ----------
elif menu == "📈 時數統計":
    st.title("📈 時數統計")

    if lessons.empty or students.empty:
        st.info("資料不足")
    else:
        df = lessons.copy()
        df["date"] = parse_date_safe(df["date"])
        df = df.dropna(subset=["date"])
        df["start_dt"] = pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + df["start"], errors="coerce")
        df["end_dt"] = pd.to_datetime(df["date"].dt.strftime("%Y-%m-%d") + " " + df["end"], errors="coerce")
        df["hours"] = (df["end_dt"] - df["start_dt"]).dt.total_seconds() / 3600
        name_map = students.set_index("id")["name"].to_dict()
        df["學生"] = df["student_id"].map(name_map)

        months = sorted(df["date"].dt.strftime("%Y-%m").dropna().unique(), reverse=True)
        month = st.selectbox("選擇月份", ["全部"] + list(months))
        if month != "全部":
            df = df[df["
