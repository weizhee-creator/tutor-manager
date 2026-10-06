# -*- coding: utf-8 -*-
import os
import time
os.environ["TZ"] = "Asia/Taipei"
try:
    time.tzset()
except AttributeError:
    pass

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import datetime, date, timedelta
import gspread
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io
import calendar

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

# ---------- 學生顏色 ----------
STUDENT_COLORS = [
    "#FF6B6B", "#FFA94D", "#FFD43B", "#51CF66", "#22B8CF",
    "#4DABF7", "#9775FA", "#F783AC", "#A0784C", "#2F9E44",
    "#1971C2", "#6741D9", "#5C7CFA", "#E599F7", "#FFA8A8",
]

def get_student_color(student_id, all_student_ids):
    try:
        idx = list(all_student_ids).index(student_id)
        return STUDENT_COLORS[idx % len(STUDENT_COLORS)]
    except (ValueError, TypeError):
        return "#CCCCCC"

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

LESSON_TYPES = ["正課", "補課", "試聽", "加課", "調課"]

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
    "🗓️ 月曆檢視",
    "📊 上課進度",
    "👤 學生管理",
    "🔄 補課管理",
    "🔀 調課管理",
    "📁 檔案管理",
    "📋 申請審核",
    "📅 課程排程",
    "📆 週期排課",
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

# ---------- 月曆檢視 ----------
elif menu == "🗓️ 月曆檢視":
    st.title("🗓️ 月曆檢視")

    if lessons.empty:
        st.info("尚無課程")
    else:
        today = date.today()
        if "cal_year" not in st.session_state:
            st.session_state.cal_year = today.year
        if "cal_month" not in st.session_state:
            st.session_state.cal_month = today.month

        col1, col2, col3 = st.columns([1, 3, 1])
        with col1:
            if st.button("◀ 上個月"):
                if st.session_state.cal_month == 1:
                    st.session_state.cal_month = 12
                    st.session_state.cal_year -= 1
                else:
                    st.session_state.cal_month -= 1
                st.rerun()
        with col2:
            st.markdown(f"<h2 style='text-align: center;'>{st.session_state.cal_year} 年 {st.session_state.cal_month} 月</h2>", unsafe_allow_html=True)
        with col3:
            if st.button("下個月 ▶"):
                if st.session_state.cal_month == 12:
                    st.session_state.cal_month = 1
                    st.session_state.cal_year += 1
                else:
                    st.session_state.cal_month += 1
                st.rerun()

        lessons_cal = lessons.copy()
        lessons_cal["date_parsed"] = parse_date_safe(lessons_cal["date"])
        lessons_cal = lessons_cal.dropna(subset=["date_parsed"])
        lessons_cal = lessons_cal[
            (lessons_cal["date_parsed"].dt.year == st.session_state.cal_year) &
            (lessons_cal["date_parsed"].dt.month == st.session_state.cal_month)
        ]

        name_map = students.set_index("id")["name"].to_dict() if not students.empty else {}
        student_ids = list(students["id"]) if not students.empty else []
        lessons_cal["學生"] = lessons_cal["student_id"].map(name_map)

        lessons_by_date = {}
        for _, row in lessons_cal.iterrows():
            d = row["date_parsed"].date()
            if d not in lessons_by_date:
                lessons_by_date[d] = []
            lessons_by_date[d].append(row)

        for d in lessons_by_date:
            lessons_by_date[d] = sorted(
                lessons_by_date[d],
                key=lambda x: str(x.get("start", ""))
            )

        year = st.session_state.cal_year
        month = st.session_state.cal_month
        first_day = date(year, month, 1)
        days_in_month = calendar.monthrange(year, month)[1]
        first_weekday = first_day.weekday()

        html = """
        <style>
        .cal-container { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif; }
        .cal-table { width: 100%; border-collapse: collapse; table-layout: fixed; }
        .cal-table th { background: #F0F2F6; padding: 8px 4px; text-align: center; font-weight: 600; font-size: 14px; color: #333; border: 1px solid #E0E0E0; }
        .cal-table td { border: 1px solid #E0E0E0; padding: 4px; vertical-align: top; height: 110px; width: 14.28%; }
        .cal-day-num { font-weight: bold; font-size: 14px; color: #333; margin-bottom: 4px; }
        .cal-today { background: #FFF8E1; }
        .cal-lesson { display: block; font-size: 11px; padding: 2px 4px; margin-bottom: 2px; border-radius: 3px; color: white; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .cal-legend { margin-top: 16px; padding: 12px; background: #F8F9FA; border-radius: 8px; }
        .cal-legend-item { display: inline-block; margin-right: 16px; margin-bottom: 6px; font-size: 13px; }
        .cal-legend-dot { display: inline-block; width: 12px; height: 12px; border-radius: 50%; margin-right: 4px; vertical-align: middle; }
        </style>
        <div class="cal-container">
        <table class="cal-table">
        <thead>
        <tr><th>一</th><th>二</th><th>三</th><th>四</th><th>五</th><th>六</th><th>日</th></tr>
        </thead>
        <tbody>
        """

        total_cells = first_weekday + days_in_month
        total_weeks = (total_cells + 6) // 7

        current_day = 1
        today_date = date.today()

        for week in range(total_weeks):
            html += "<tr>"
            for wd in range(7):
                cell_index = week * 7 + wd
                if cell_index < first_weekday or current_day > days_in_month:
                    html += "<td></td>"
                else:
                    d = date(year, month, current_day)
                    is_today = (d == today_date)
                    td_class = ' class="cal-today"' if is_today else ""
                    html += f'<td{td_class}>'
                    html += f'<div class="cal-day-num">{current_day}</div>'

                    if d in lessons_by_date:
                        for lesson in lessons_by_date[d][:4]:
                            color = get_student_color(lesson["student_id"], student_ids)
                            name = lesson.get("學生", "")
                            start_t = lesson.get("start", "")
                            ltype = lesson.get("type", "")
                            title = f"{start_t} {name} {ltype}"
                            html += f'<span class="cal-lesson" style="background:{color};" title="{title}">{title}</span>'

                        if len(lessons_by_date[d]) > 4:
                            html += f'<div style="font-size:10px;color:#666;">+{len(lessons_by_date[d]) - 4} 更多</div>'

                    html += "</td>"
                    current_day += 1
            html += "</tr>"

        html += "</tbody></table>"
        html += '<div class="cal-legend"><strong>學生顏色對應：</strong><br>'
        if not students.empty:
            for i, (_, s) in enumerate(students.iterrows()):
                color = STUDENT_COLORS[i % len(STUDENT_COLORS)]
                html += f'<span class="cal-legend-item"><span class="cal-legend-dot" style="background:{color};"></span>{s["name"]}</span>'
        html += "</div></div>"

        components.html(html, height=800, scrolling=True)

        st.markdown("---")
        st.subheader("📋 查看某日課程")

        if not lessons_cal.empty:
            available_dates = sorted(lessons_by_date.keys())
            date_options = [d.strftime("%Y-%m-%d（週" + ['一', '二', '三', '四', '五', '六', '日'][d.weekday()] + "）") for d in available_dates]
            selected_date_str = st.selectbox("選擇日期", date_options)
            selected_idx = date_options.index(selected_date_str)
            selected_date = available_dates[selected_idx]

            st.markdown(f"### 📅 {selected_date.strftime('%Y-%m-%d')}（週{['一', '二', '三', '四', '五', '六', '日'][selected_date.weekday()]}）")

            day_lessons = lessons_by_date[selected_date]
            for lesson in day_lessons:
                color = get_student_color(lesson["student_id"], student_ids)
                with st.container():
                    c1, c2, c3, c4 = st.columns([1, 2, 2, 2])
                    with c1:
                        st.markdown(f'<div style="background:{color};width:20px;height:20px;border-radius:50%;"></div>', unsafe_allow_html=True)
                    with c2:
                        st.markdown(f"**{lesson.get('start')}-{lesson.get('end')}**")
                    with c3:
                        st.markdown(f"**{lesson.get('學生', '')}**　{lesson.get('type', '')}")
                    with c4:
                        st.markdown(f"{lesson.get('status', '')}")
                    if lesson.get("note"):
                        st.caption(f"📝 {lesson['note']}")
        else:
            st.info("這個月沒有課程")

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
        show_students = students[["name", "subject", "hourly_rate", "note"]].copy()
        show_students.columns = ["姓名", "科目", "時薪", "備註"]
        st.dataframe(show_students, use_container_width=True, hide_index=True)

        st.markdown("---")
        st.subheader("🗑️ 刪除學生")
        del_options = {f"{row['name']}（{row.get('subject', '')}）": int(row["id"]) for _, row in students.iterrows()}
        selected_del = st.selectbox("選擇要刪除的學生", list(del_options.keys()))
        del_id = del_options[selected_del]
        confirm = st.checkbox("我確認要刪除這位學生", key="confirm_del_student")
        if st.button("🗑️ 確定刪除", disabled=not confirm, type="primary"):
            students = students[students["id"] != del_id]
            save_data(students, "students")
            st.success("已刪除")
            st.rerun()

# ---------- 補課管理 ----------

elif menu == "🔄 補課管理":
    st.title("🔄 補課管理")

    st.subheader("標記請假課程")
    if lessons.empty:
        st.info("尚無課程可操作")
    else:
        pending = lessons[lessons["status"] == "已排定"].copy()
        if not pending.empty:
            name_map = students.set_index("id")["name"].to_dict() if not students.empty else {}
            pending["學生"] = pending["student_id"].map(name_map)
            pending["date_parsed"] = parse_date_safe(pending["date"])

            pending_options = {}
            for _, row in pending.sort_values("date_parsed").iterrows():
                d_str = row["date_parsed"].strftime("%Y-%m-%d") if pd.notna(row["date_parsed"]) else str(row.get("date", ""))
                label = f"{d_str} {row.get('start', '')}-{row.get('end', '')} {row.get('學生', '')}（{row.get('type', '')}）"
                pending_options[label] = int(row["id"])

            selected = st.selectbox("選擇要請假的課程", list(pending_options.keys()))
            lid = pending_options[selected]

            if st.button("標記為待補課"):
                lessons.loc[lessons["id"] == lid, "status"] = "待補課"
                save_data(lessons, "lessons")
                st.success("✅ 已標記為待補課")
                st.rerun()
        else:
            st.info("目前沒有已排定的課程")

    st.markdown("---")

    # ---------- 待補課清單 ----------
    st.subheader("待補課清單")
    todo = lessons[lessons["status"] == "待補課"]
    if todo.empty:
        st.success("沒有待補課項目 🎉")
    else:
        todo_view = todo.copy()
        name_map = students.set_index("id")["name"].to_dict() if not students.empty else {}
        todo_view["學生"] = todo_view["student_id"].map(name_map)
        show_todo = todo_view[["date", "start", "end", "學生", "type", "note"]].copy()
        show_todo.columns = ["日期", "開始", "結束", "學生", "類型", "備註"]
        st.dataframe(show_todo, use_container_width=True, hide_index=True)

        st.markdown("### 安排補課")
        todo = todo.copy()
        todo["date_parsed"] = parse_date_safe(todo["date"])
        todo_options = {}
        for _, row in todo.sort_values("date_parsed").iterrows():
            d_str = row["date_parsed"].strftime("%Y-%m-%d") if pd.notna(row["date_parsed"]) else str(row.get("date", ""))
            label = f"{d_str} {row.get('start', '')}-{row.get('end', '')} {row.get('學生', '')}"
            todo_options[label] = int(row["id"])

        with st.form("makeup"):
            selected_todo = st.selectbox("選擇要補的課程", list(todo_options.keys()))
            lid = todo_options[selected_todo]
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
                st.success("✅ 補課已建立")
                st.rerun()

        
# ---------- 取消待補課標記 ----------
        st.markdown("---")
        st.subheader("↩️ 取消待補課標記")
        st.caption("將課程恢復為「已排定」")

        # 建立含學生名字的下拉選單
        cancel_options = {}
        for _, row in todo.sort_values("date_parsed").iterrows():
            d_str = row["date_parsed"].strftime("%Y-%m-%d") if pd.notna(row["date_parsed"]) else str(row.get("date", ""))
            label = f"{d_str} {row.get('start', '')}-{row.get('end', '')} {row.get('學生', '')}（{row.get('type', '')}）"
            cancel_options[label] = int(row["id"])

        with st.form("cancel_todo"):
            selected_cancel = st.selectbox("選擇要取消標記的課程", list(cancel_options.keys()), key="cancel_todo_select")
            cancel_lid = cancel_options[selected_cancel]

            if st.form_submit_button("↩️ 取消待補課", type="primary"):
                lessons.loc[lessons["id"] == cancel_lid, "status"] = "已排定"
                save_data(lessons, "lessons")
                st.success("✅ 已取消待補課標記，恢復為「已排定」")
                st.rerun()

# ---------- 調課管理 ----------
elif menu == "🔀 調課管理":
    st.title("🔀 調課管理")
    st.caption("將原課程調到新時間，系統會自動標記原課為已調課")

    if lessons.empty or students.empty:
        st.info("尚無課程可調")
    else:
        name_map = students.set_index("id")["name"].to_dict()
        lessons_avail = lessons[lessons["status"].astype(str) == "已排定"].copy()
        lessons_avail["學生"] = lessons_avail["student_id"].map(name_map)
        lessons_avail["date_parsed"] = parse_date_safe(lessons_avail["date"])
        lessons_avail = lessons_avail.dropna(subset=["date_parsed"])
        lessons_avail = lessons_avail.sort_values("date_parsed")

        if lessons_avail.empty:
            st.info("目前沒有可調的課程（已排定）")
        else:
            st.subheader("📋 選擇要調的課程")
            options = {}
            for _, row in lessons_avail.iterrows():
                d_str = row["date_parsed"].strftime("%Y-%m-%d")
                label = f"{d_str} {row.get('start', '')}-{row.get('end', '')} {row.get('學生', '')}（{row.get('type', '')}）"
                options[label] = int(row["id"])

            selected = st.selectbox("原課程", list(options.keys()))
            old_lesson_id = options[selected]
            old_lesson = lessons[lessons["id"] == old_lesson_id].iloc[0]

            st.info(f"📌 原課程：{old_lesson['date']} {old_lesson['start']}-{old_lesson['end']}，學生：{name_map.get(int(old_lesson['student_id']), '')}")

            st.markdown("---")
            st.subheader("🆕 新時間")

            with st.form("reschedule_form"):
                new_date = st.date_input("新日期", value=date.today())
                c1, c2 = st.columns(2)
                new_start = c1.time_input("新開始時間", value=datetime.strptime(old_lesson["start"], "%H:%M").time())
                new_end = c2.time_input("新結束時間", value=datetime.strptime(old_lesson["end"], "%H:%M").time())
                reason = st.text_input("調課原因（可選）")

                if st.form_submit_button("🔀 確認調課", type="primary"):
                    old_note = str(lessons.loc[lessons["id"] == old_lesson_id, "note"].values[0]) if "note" in lessons.columns else ""
                    lessons.loc[lessons["id"] == old_lesson_id, "status"] = "已調課"
                    lessons.loc[lessons["id"] == old_lesson_id, "note"] = (old_note + f" | 調至 {new_date} {new_start}").strip(" |")

                    new_id = safe_next_id(lessons)
                    note_text = f"原課程 ID {old_lesson_id}"
                    if reason:
                        note_text += f"（{reason}）"

                    new_row = pd.DataFrame([[
                        new_id,
                        int(old_lesson["student_id"]),
                        str(new_date),
                        new_start.strftime("%H:%M"),
                        new_end.strftime("%H:%M"),
                        "調課",
                        "已排定",
                        note_text
                    ]], columns=LESSON_COLS)

                    lessons = pd.concat([lessons, new_row], ignore_index=True)
                    save_data(lessons, "lessons")

                    st.success(f"✅ 調課成功！")
                    st.rerun()

# ---------- 檔案管理 ----------
elif menu == "📁 檔案管理":
    st.title("📁 檔案管理")
    st.caption("為每位學生管理專屬資料夾，可自由新增分類")

    if students.empty:
        st.warning("請先新增學生")
    else:
        student = st.selectbox("選擇學生", students["name"])
        sid = int(students[students["name"] == student]["id"].values[0])
        student_row = students[students["id"] == sid].iloc[0]

        try:
            service = get_drive_service()
            folder_id = get_student_folder(service, student_row)
            st.success(f"📁 學生資料夾：{student}")

            st.markdown("---")

            # ---------- 新增資料夾 ----------
            st.subheader("➕ 新增資料夾")
            with st.form("create_folder_form"):
                new_folder_name = st.text_input("資料夾名稱", placeholder="例如：數學、段考複習、作業")
                if st.form_submit_button("✅ 建立資料夾"):
                    if not new_folder_name.strip():
                        st.error("請輸入資料夾名稱")
                    else:
                        existing = service.files().list(
                            q=f"name='{new_folder_name}' and mimeType='application/vnd.google-apps.folder' and '{folder_id}' in parents and trashed=false",
                            fields="files(id, name)"
                        ).execute().get("files", [])

                        if existing:
                            st.warning(f"⚠️ 資料夾「{new_folder_name}」已存在")
                        else:
                            try:
                                metadata = {
                                    "name": new_folder_name,
                                    "mimeType": "application/vnd.google-apps.folder",
                                    "parents": [folder_id]
                                }
                                service.files().create(body=metadata, fields="id").execute()
                                st.success(f"✅ 已建立資料夾「{new_folder_name}」")
                                st.rerun()
                            except Exception as e:
                                st.error(f"建立失敗：{e}")

            st.markdown("---")

            # ---------- 上傳檔案 ----------
            st.subheader("⬆️ 上傳檔案")

            subfolders = service.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)",
                orderBy="name"
            ).execute().get("files", [])

            if not subfolders:
                st.info("💡 請先建立資料夾，才能上傳檔案")
            else:
                folder_options = {sf["name"]: sf["id"] for sf in subfolders}
                selected_folder_name = st.selectbox("選擇要上傳到的資料夾", list(folder_options.keys()), key="upload_folder_select")
                selected_folder_id = folder_options[selected_folder_name]

                uploaded_files = st.file_uploader(
                    "選擇檔案（可一次選多個）",
                    accept_multiple_files=True,
                    key="teacher_upload"
                )

                if uploaded_files:
                    st.caption(f"已選擇 {len(uploaded_files)} 個檔案：")
                    for uf in uploaded_files:
                        st.caption(f"　📄 {uf.name}（{uf.size / 1024:.1f} KB）")

                    if st.button("📤 全部上傳", type="primary"):
                        success_count = 0
                        fail_count = 0
                        fail_files = []

                        progress_bar = st.progress(0)
                        status_text = st.empty()

                        for i, uf in enumerate(uploaded_files):
                            status_text.text(f"上傳中... ({i+1}/{len(uploaded_files)}) {uf.name}")
                            try:
                                upload_file(
                                    service,
                                    selected_folder_id,
                                    uf.name,
                                    uf.getvalue(),
                                    uf.type or "application/octet-stream"
                                )
                                success_count += 1
                            except Exception as e:
                                fail_count += 1
                                fail_files.append(f"{uf.name}: {e}")

                            progress_bar.progress((i + 1) / len(uploaded_files))

                        status_text.empty()
                        progress_bar.empty()

                        if success_count > 0:
                            st.success(f"✅ 成功上傳 {success_count} 個檔案")
                        if fail_count > 0:
                            st.error(f"❌ {fail_count} 個檔案失敗")
                            for ff in fail_files:
                                st.caption(f"　{ff}")

                        st.rerun()

            st.markdown("---")

            # ---------- 資料夾與檔案列表 ----------
            st.subheader("📂 資料夾與檔案")

            if not subfolders:
                st.info("尚未建立任何資料夾")
            else:
                for subfolder in subfolders:
                    files = list_files(service, subfolder["id"])
                    file_count = len(files)

                    with st.expander(f"📁 {subfolder['name']}（{file_count} 個檔案）", expanded=False):
                        col_del1, col_del2 = st.columns([3, 1])
                        with col_del2:
                            if st.button("🗑️ 刪除資料夾", key=f"del_folder_{subfolder['id']}"):
                                st.session_state[f"confirm_del_folder_{subfolder['id']}"] = True

                        if st.session_state.get(f"confirm_del_folder_{subfolder['id']}", False):
                            st.warning(f"⚠️ 確定要刪除「{subfolder['name']}」嗎？裡面 {file_count} 個檔案也會一起刪除（無法復原）")
                            col_c1, col_c2 = st.columns(2)
                            with col_c1:
                                if st.button("✅ 確定刪除", key=f"real_del_folder_{subfolder['id']}", type="primary"):
                                    try:
                                        for f in files:
                                            try:
                                                delete_file(service, f["id"])
                                            except:
                                                pass
                                        service.files().delete(fileId=subfolder["id"]).execute()
                                        st.success(f"✅ 已刪除資料夾「{subfolder['name']}」")
                                        st.session_state[f"confirm_del_folder_{subfolder['id']}"] = False
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"刪除失敗：{e}")
                            with col_c2:
                                if st.button("❌ 取消", key=f"cancel_del_folder_{subfolder['id']}"):
                                    st.session_state[f"confirm_del_folder_{subfolder['id']}"] = False
                                    st.rerun()

                        st.markdown("---")

                        if not files:
                            st.caption("（此資料夾為空）")
                        else:
                            for f in files:
                                c1, c2, c3 = st.columns([4, 1, 1])
                                with c1:
                                    size_kb = int(f.get("size", 0)) / 1024 if f.get("size") else 0
                                    st.markdown(f"📄 **{f['name']}** （{size_kb:.1f} KB）")
                                with c2:
                                    st.markdown(f"[🔗 開啟]({f.get('webViewLink', '#')})")
                                with c3:
                                    if st.button("🗑️", key=f"del_file_{f['id']}"):
                                        try:
                                            delete_file(service, f["id"])
                                            st.success("已刪除")
                                            st.rerun()
                                        except Exception as e:
                                            st.error(f"刪除失敗：{e}")

        except Exception as e:
            st.error(f"Drive 連線失敗：{e}")

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
                show_history = history[["學生", "type", "status", "created_at", "teacher_note"]].copy()
                show_history.columns = ["學生", "類型", "狀態", "送出時間", "老師備註"]
                st.dataframe(show_history, use_container_width=True, hide_index=True)

# ---------- 課程排程 ----------
elif menu == "📅 課程排程":
    st.title("📅 課程排程")

    if students.empty:
        st.warning("請先新增學生")
    else:
        with st.expander("➕ 新增課程", expanded=False):
            with st.form("add_lesson"):
                student = st.selectbox("學生", students["name"])
                sid = int(students[students["name"] == student]["id"].values[0])
                d = st.date_input("日期", value=date.today())
                c1, c2 = st.columns(2)
                start = c1.time_input("開始時間", value=datetime.strptime("19:00", "%H:%M").time())
                end = c2.time_input("結束時間", value=datetime.strptime("20:00", "%H:%M").time())
                ltype = st.selectbox("類型", LESSON_TYPES)
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
            st.caption("💡 點擊「備註」欄位即可編輯，編輯後按下方「💾 儲存變更」")
            edit_cols = ["id", "date", "start", "end", "學生", "type", "status", "note"]
            edit_df = view[edit_cols].copy()

            edited = st.data_editor(
                edit_df,
                use_container_width=True,
                hide_index=True,
                disabled=["id", "date", "start", "end", "學生", "type", "status"],
                column_config={
                    "id": None,
                    "date": st.column_config.TextColumn("日期", width="small"),
                    "start": st.column_config.TextColumn("開始", width="small"),
                    "end": st.column_config.TextColumn("結束", width="small"),
                    "學生": st.column_config.TextColumn("學生", width="small"),
                    "type": st.column_config.TextColumn("類型", width="small"),
                    "status": st.column_config.TextColumn("狀態", width="small"),
                    "note": st.column_config.TextColumn("備註", width="large"),
                },
                key="lesson_editor",
            )

            if st.button("💾 儲存變更", type="primary"):
                changed = False
                for _, row in edited.iterrows():
                    lid = int(row["id"])
                    new_note = str(row["note"]) if pd.notna(row["note"]) else ""
                    old_rows = lessons[lessons["id"] == lid]
                    old_note = str(old_rows["note"].values[0]) if not old_rows.empty else ""

                    if new_note != old_note:
                        lessons.loc[lessons["id"] == lid, "note"] = new_note
                        changed = True

                if changed:
                    save_data(lessons, "lessons")
                    st.success("✅ 備註已更新")
                    st.rerun()
                else:
                    st.info("沒有變更")

        st.markdown("---")
        st.subheader("🗑️ 刪除課程")
        st.caption("刪除時會一併刪除相關的請假申請與進度紀錄")

        if view.empty:
            st.info("沒有課程可刪除")
        else:
            del_options = {}
            for _, row in view.iterrows():
                label = f"{row.get('date', '')} {row.get('start', '')}-{row.get('end', '')} {row.get('學生', '')} ({row.get('type', '')})"
                del_options[label] = int(row["id"])

            selected_del = st.selectbox("選擇要刪除的課程", list(del_options.keys()))
            del_id = del_options[selected_del]

            st.warning(f"⚠️ 將刪除該課程及其相關的請假申請與進度紀錄")
            confirm = st.checkbox("我確認要刪除這堂課（無法復原）", key="confirm_del_lesson")

            if st.button("🗑️ 確定刪除", disabled=not confirm, type="primary"):
                lessons = lessons[lessons["id"] != del_id]
                save_data(lessons, "lessons")

                if not requests_df.empty and "original_lesson_id" in requests_df.columns:
                    before = len(requests_df)
                    requests_df = requests_df[
                        pd.to_numeric(requests_df["original_lesson_id"], errors="coerce") != del_id
                    ]
                    if len(requests_df) != before:
                        save_data(requests_df, "requests")

                if not progress_df.empty and "lesson_id" in progress_df.columns:
                    before = len(progress_df)
                    progress_df = progress_df[
                        pd.to_numeric(progress_df["lesson_id"], errors="coerce") != del_id
                    ]
                    if len(progress_df) != before:
                        save_data(progress_df, "progress")

                st.success("✅ 已刪除課程")
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
            df = df[df["date"].dt.strftime("%Y-%m") == month]

        summary = df.groupby("學生")["hours"].sum().reset_index()
        summary.columns = ["學生", "總時數"]
        rate_map = students.set_index("name")["hourly_rate"].to_dict()
        summary["預估收入"] = summary.apply(lambda r: r["總時數"] * rate_map.get(r["學生"], 0), axis=1)
        st.metric("總收入", f"NT$ {int(summary['預估收入'].sum()):,}")
        st.dataframe(summary, use_container_width=True, hide_index=True)

        import plotly.express as px
        fig = px.bar(summary, x="學生", y="總時數", title="各學生上課時數")
        st.plotly_chart(fig, use_container_width=True)
