import hashlib
import os
import threading
import time
import uuid
from datetime import datetime

import requests
import streamlit as st
from flask import Flask, request, jsonify, Response
from werkzeug.utils import secure_filename

# ================== 全局配置 ==================
# 文件存储根目录
UPLOAD_FOLDER = './cloud_disk/uploads'
# 文件元数据
METADATA_FILE = './cloud_disk/metadata.txt'
# 单文件上传最大大小 50MB
MAX_FILE_SIZE = 50 * 1024 * 1024
# 云文件存储最大存储大小 10G
CLOUD_DISK_MAX_STORAGE_SIZE = 10 * 1024 * 1024 * 1024
# 网盘文件目录查看密码
LIST_PASSWORD_HASH = hashlib.sha256('salt_pass_imfun'.encode()).hexdigest()
# flask应用启动端口
FLASK_APP_PORT = 5001
# streamlit应用启动端口
STREAMLIT_APP_PORT = 8501
# 本地测试
DOMAIN = f"http://127.0.0.1:{FLASK_APP_PORT}"
# streamlit应用根路径
STREAMLIT_BASE_PATH = "/c1yunpan"
# flask应用根路径
FLASK_BASE_PATH = "/c1yunpan/api"
# flask云应用路径
FLASK_CLOUD_PATH = f"{DOMAIN}{FLASK_BASE_PATH}"

# 存储临时令牌 {token: expiry_time}
TOKENS = {}

# ================== Flask API部分 ==================
flask_app = Flask(__name__)


class StorageManager:
    def __init__(self):
        self.lock = threading.Lock()

    def get_storage_usage(self):
        total_size = 0
        if not os.path.exists(METADATA_FILE):
            return 0
        with open(METADATA_FILE, 'r') as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(':')
                    total_size += int(parts[3])
        return total_size

    def check_storage(self, file_size):
        with self.lock:
            current_usage = self.get_storage_usage()
            return (current_usage + file_size) <= CLOUD_DISK_MAX_STORAGE_SIZE


storage_manager = StorageManager()


def cleanup_task():
    while True:
        try:
            now = time.time()
            new_lines = []
            # 清理过期文件
            if os.path.exists(METADATA_FILE):
                with open(METADATA_FILE, 'r') as f:
                    lines = f.readlines()

                for line in lines:
                    if line.strip():
                        parts = line.strip().split(':')
                        filename = parts[0]
                        expire_time = float(parts[4])
                        if expire_time != 0 and now > expire_time:
                            filepath = os.path.join(UPLOAD_FOLDER, filename)
                            if os.path.exists(filepath):
                                os.remove(filepath)
                        else:
                            new_lines.append(line)

                with open(METADATA_FILE, 'w') as f:
                    f.writelines(new_lines)

            # 清理过期令牌
            global TOKENS
            TOKENS = {k: v for k, v in TOKENS.items() if v > now}

        except Exception as e:
            print(f"清理出错: {str(e)}")
        time.sleep(60)


cleanup_thread = threading.Thread(target=cleanup_task, daemon=True)
cleanup_thread.start()


# 删除接口
@flask_app.route(f'{FLASK_BASE_PATH}/delete-file', methods=['POST'])
def delete_file():
    data = request.json
    token = data['token']

    if not token or TOKENS.get(token, 0) < time.time():
        return jsonify({"error": "重新进入云盘列表"}), 401

    filename = data['filename']
    received_hash_pass = data['password']

    valid = False
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            for line in f:
                parts = line.strip().split(':')
                if parts[0] == filename and parts[1] == received_hash_pass:
                    valid = True
                    break

    if valid:
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(filepath):
            os.remove(filepath)

        # 更新元数据
        new_lines = []
        if os.path.exists(METADATA_FILE):
            with open(METADATA_FILE, 'r') as f:
                for line in f:
                    if line.strip() and line.strip().split(':')[0] != filename:
                        new_lines.append(line)
            with open(METADATA_FILE, 'w') as f:
                f.writelines(new_lines)
    else:
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(filepath):
            os.remove(filepath)

    return jsonify({"message": "删除成功"})


# 令牌验证接口
@flask_app.route(f'{FLASK_BASE_PATH}/token', methods=['POST'])
def generate_token():
    password = request.json.get('password')
    if password != LIST_PASSWORD_HASH:
        return jsonify({"error": "密码错误"}), 401

    token = hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()
    TOKENS[token] = time.time() + 600  # 10分钟有效期
    return jsonify({"token": token})


# 文件上传
@flask_app.route(f'{FLASK_BASE_PATH}/upload', methods=['POST'])
def upload_file():
    token = request.form['token']
    if not token or TOKENS.get(token, 0) < time.time():
        return jsonify({"error": "重新进入云盘列表"}), 401

    file = request.files['file']
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)

    if file_size > MAX_FILE_SIZE:
        return jsonify({"error": "文件超过50MB限制"}), 400

    if not storage_manager.check_storage(file_size):
        return jsonify({"error": "存储空间不足"}), 400

    hashed_password = request.form['password']
    expire_option = request.form['expire']

    # 校验密码唯一性
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            for line in f:
                if line.strip().split(':')[1] == hashed_password:
                    return jsonify({"error": "密码处理失败，请更换其他密码"}), 400

    upload_time = time.time()
    expire_map = {
        '10m': 600,
        '30m': 1800,
        '1d': 86400,
        '3d': 259200,
        '7d': 604800,
        'forever': 0
    }
    expire_seconds = expire_map.get(expire_option, -1)
    if expire_seconds == -1:
        return jsonify({"error": "非法保存时间"}), 400

    expire_time = upload_time + expire_seconds if expire_seconds else 0

    raw_filename = secure_filename(file.filename)  # 自动过滤危险字符
    if raw_filename != file.filename:
        return jsonify({"error": "非法文件名"}), 400

    save_path = os.path.join(UPLOAD_FOLDER, raw_filename)
    file.save(save_path)

    metadata_line = f"{raw_filename}:{hashed_password}:{upload_time}:{file_size}:{expire_time}\n"
    with open(METADATA_FILE, 'a') as f:
        f.write(metadata_line)

    return jsonify({"message": "上传成功", "filename": raw_filename})


# 文件列表接口（分页）
@flask_app.route(f'{FLASK_BASE_PATH}/files')
def list_files():
    token = request.args.get('token')
    if not token or TOKENS.get(token, 0) < time.time():
        return jsonify({"error": "重新进入云盘列表"}), 401

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 10))
    search = request.args.get('search', '')

    files = []
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            lines = sorted(f.readlines(),
                           key=lambda x: -float(x.split(':')[2]))  # 按上传时间倒序

            for line in lines:
                parts = line.strip().split(':')
                filename = parts[0]
                if search.lower() in filename.lower():
                    files.append({
                        "name": filename,
                        "size": int(parts[3]),
                        "upload_time": float(parts[2]),
                        "expire_time": float(parts[4])
                    })

    total = len(files)
    start = (page - 1) * per_page
    end = start + per_page
    return jsonify({
        "files": files[start:end],
        "total": total,
        "page": page,
        "per_page": per_page
    })


# 密码直接下载接口
@flask_app.route(f'{FLASK_BASE_PATH}/download-by-pass', methods=['POST'])
def download_by_password():
    data = request.json
    token = data.get('token')
    if not token or TOKENS.get(token, 0) < time.time():
        return jsonify({"error": "重新进入云盘列表"}), 401

    hashed_pass = data.get('password')

    target_file = None
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            for line in f:
                parts = line.strip().split(':')
                if parts[1] == hashed_pass:
                    target_file = parts[0]
                    break

    if not target_file:
        return jsonify({"error": "文件不存在1"}), 404

    filepath = os.path.join(UPLOAD_FOLDER, target_file)
    if not os.path.exists(filepath):
        return jsonify({"error": "文件不存在2"}), 404

    def generate():
        with open(filepath, 'rb') as f:
            while chunk := f.read(4096):
                yield chunk

    return Response(
        generate(),
        headers={
            'Content-Disposition': f'attachment; filename="{target_file}"',
            'x-c1-filename': target_file,
        },
        mimetype='application/octet-stream'
    )


# 文件下载
@flask_app.route(f'{FLASK_BASE_PATH}/download/<filename>')
def download_file(filename):
    token = request.args.get('token')
    if not token or TOKENS.get(token, 0) < time.time():
        return jsonify({"error": "重新进入云盘列表"}), 401

    received_hash_pass = request.args.get('password')
    filepath = os.path.join(UPLOAD_FOLDER, filename)

    # 增加文件所有权校验
    if not os.path.realpath(filepath).startswith(os.path.realpath(UPLOAD_FOLDER)):
        return jsonify({"error": "非法文件"}), 403

    if not os.path.exists(filepath):
        return jsonify({"error": "文件不存在"}), 404

    valid = False
    if os.path.exists(METADATA_FILE):
        with open(METADATA_FILE, 'r') as f:
            for line in f:
                parts = line.strip().split(':')
                if parts[0] == filename and parts[1] == received_hash_pass:
                    valid = True
                    break

    if valid:
        def generate():
            with open(filepath, 'rb') as f:
                while chunk := f.read(4096):
                    yield chunk

        return Response(
            generate(),
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            mimetype='application/octet-stream'
        )
    else:
        return jsonify({"error": "密码错误"}), 401


@flask_app.route(f'{FLASK_BASE_PATH}/status')
def system_status():
    token = request.args.get('token')
    if not token or TOKENS.get(token, 0) < time.time():
        return jsonify({"error": "重新进入云盘列表"}), 401

    usage = storage_manager.get_storage_usage()
    return jsonify({
        "max_storage": CLOUD_DISK_MAX_STORAGE_SIZE,
        "used_storage": usage,
        "file_count": len(os.listdir(UPLOAD_FOLDER)) if os.path.exists(UPLOAD_FOLDER) else 0
    })


# ================== Streamlit UI部分 ==================
def format_time(seconds):
    if seconds == 0:
        return "永久"
    periods = [('天', 86400), ('小时', 3600), ('分钟', 60)]
    result = []
    for name, sec in periods:
        if seconds >= sec:
            val, seconds = divmod(seconds, sec)
            result.append(f"{int(val)}{name}")
    return ' '.join(result) if result else "不足1分钟"


def format_file_size(size_in_bytes):
    """
    将文件大小从字节转换为 KB、MB 或 GB 的格式，并以最小的单位显示。
    :param size_in_bytes: 文件大小（以字节为单位）
    :return: 格式化后的文件大小字符串
    """
    units = ['B', 'KB', 'MB', 'GB']
    size = size_in_bytes  # 初始大小单位是 Bytes
    unit_index = 0  # 初始单位是 Bytes

    # 逐步转换单位，直到文件大小小于 1000
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    # 格式化输出，保留两位小数
    return f"{size:.2f}{units[unit_index]}"


def streamlit_ui():
    # 初始化会话状态
    if 'token' not in st.session_state:
        st.session_state.token = None

    st.set_page_config(
        page_title="C1云盘",
        page_icon="📁",
        layout="centered" if st.session_state.token == None else "wide"
    )

    st.header("C1云盘")

    # 密码验证模块
    if not st.session_state.token:
        list_pass = st.text_input("🔑 输入云盘查看密码", type="password")
        if list_pass:
            try:
                pass_hash = hashlib.sha256(f'salt_pass_{list_pass}'.encode()).hexdigest()
                response = requests.post(
                    f"{FLASK_CLOUD_PATH}/token",
                    json={"password": pass_hash}
                )
                if response.status_code == 200:
                    st.session_state.token = response.json()['token']
                    st.rerun()
                else:
                    st.error("密码错误")
            except Exception as e:
                st.error("服务不可用")
        return

    # 系统状态
    try:
        status = requests.get(f"{FLASK_CLOUD_PATH}/status", params={"token": st.session_state.token}).json()
        used_storage = status['used_storage']
        max_storage = status['max_storage']
        used_size_str = format_file_size(used_storage)
        max_size_str = format_file_size(max_storage)
        st.progress(used_storage / max_storage,
                    f"存储使用： {used_size_str} / {max_size_str}，文件总数：{status['file_count']}")
    except Exception as e:
        st.error('云盘状态获取失败')

    # 主界面功能
    # 快速下载模块
    with st.expander("⬇️ 输入密码直接下载", expanded=True):
        dl_password = st.text_input("输入下载密码（4位数字）", max_chars=4, key="direct_download_text_input")
        if st.button("立即下载"):
            if len(dl_password) != 4 or not dl_password.isdigit():
                st.error("需4位数字")
            else:
                try:
                    pass_hash = hashlib.sha256(dl_password.encode()).hexdigest()
                    response = requests.post(
                        f"{FLASK_CLOUD_PATH}/download-by-pass",
                        json={"password": pass_hash, "token": st.session_state.token},
                        stream=True
                    )
                    if response.status_code == 200:
                        st.download_button(
                            "保存文件",
                            data=response.content,
                            file_name=f'{response.headers["x-c1-filename"]}',
                            key="direct_download"
                        )
                    else:
                        st.error(response.json().get("error"))
                except Exception as e:
                    st.error(f"下载失败")

    # 上传模块
    with st.expander("⬆️ 文件共享", expanded=False):
        if "file_uploader_counter" not in st.session_state:
            st.session_state.file_uploader_counter = 0
        if "upload_pass_counter" not in st.session_state:
            st.session_state.upload_pass_counter = 0
        if "expire_option_counter" not in st.session_state:
            st.session_state.expire_option_counter = 0

        uploaded_file = st.file_uploader(
            "选择文件（最大50MB）",
            accept_multiple_files=False,
            key=f"file_uploader_{st.session_state.file_uploader_counter}"
        )
        expire_option = st.selectbox(
            "保存时间",
            options=[('10分钟', '10m'), ('30分钟', '30m'), ('1天', '1d'),
                     ('3天', '3d'), ('7天', '7d'), ('永久', 'forever')],
            format_func=lambda x: x[0],
            key=f"expire_option_{st.session_state.expire_option_counter}"
        )
        file_pass = st.text_input("🔢 设置4位数字密码", max_chars=4,
                                  key=f"upload_pass_{st.session_state.upload_pass_counter}")

        if st.button("上传") and uploaded_file:
            if len(file_pass) != 4 or not file_pass.isdigit():
                st.error("密码必须为4位数字")
            else:
                hashed_pass = hashlib.sha256(file_pass.encode()).hexdigest()
                try:
                    response = requests.post(
                        f"{FLASK_CLOUD_PATH}/upload",
                        files={"file": (uploaded_file.name, uploaded_file)},
                        data={"password": hashed_pass, "expire": expire_option[1], "token": st.session_state.token}
                    )
                    if response.status_code == 200:
                        st.success("上传成功")
                        st.session_state.file_uploader_counter += 1
                        st.session_state.upload_pass_counter += 1
                        st.session_state.expire_option_counter += 1
                        st.rerun()

                    else:
                        st.error(response.json().get("error"))
                except Exception as e:
                    st.error(f"上传失败")

    st.subheader('文件列表')
    # 文件列表展示（分页+搜索）
    search_key = st.text_input("🔍 搜索文件名")
    col1, col2 = st.columns(2)
    page = col1.number_input("页码", min_value=1, value=1)
    per_page = col2.selectbox("每页数量", [10, 20, 50], index=0)

    try:
        response = requests.get(
            f"{FLASK_CLOUD_PATH}/files",
            params={
                "token": st.session_state.token,
                "page": page,
                "per_page": per_page,
                "search": search_key
            }
        )
        if response.status_code == 200:
            data = response.json()
            st.write(f"共 {data['total']} 个文件（输入\"下载密码\"，点击\"下载\"按钮开始下载文件）")

            for file in data['files']:
                metadata = {
                    "name": file['name'],
                    "upload_time": datetime.fromtimestamp(
                        float(file['upload_time'])).strftime('%Y-%m-%d %H:%M'),
                    "expire_time": float(file['expire_time']),
                    "size": f"{file['size']}"
                }
                remain_sec = metadata.get('expire_time', 0) - time.time()
                file_is_not_expired = remain_sec > 0
                remain_time = format_time(int(remain_sec)) if file_is_not_expired else "已过期"

                cols = st.columns([3, 1, 2, 2, 2, 1, 1], vertical_alignment="center")
                cols[0].markdown(f"**{metadata['name']}**")
                cols[1].markdown(f"{format_file_size(float(metadata.get('size', '0')))}")
                cols[2].markdown(f"{remain_time}后过期" if file_is_not_expired else f"{remain_time}")
                cols[3].markdown(f"于{metadata.get('upload_time', '-')}共享")

                if file_is_not_expired:
                    download_pass = cols[4].text_input(
                        '🔑 下载密码',
                        label_visibility="collapsed",
                        placeholder="🔑 下载密码",
                        key=f"pass_{metadata["name"]}",
                        max_chars=4
                    )
                    if cols[5].button("⬇️ 下载", key=f"btn_{metadata["name"]}") and download_pass is not None:
                        if len(download_pass) != 4 or not download_pass.isdigit():
                            cols[5].error("需4位数字")
                        else:
                            hashed_dl = hashlib.sha256(download_pass.encode()).hexdigest()
                        try:
                            response = requests.get(
                                f"{FLASK_CLOUD_PATH}/download/{metadata["name"]}",
                                params={"password": hashed_dl, "token": st.session_state.token},
                                stream=True
                            )
                            if response.status_code == 200:
                                cols[5].download_button(
                                    "保存",
                                    data=response.content,
                                    file_name=metadata["name"],
                                    key=f"dl_{metadata["name"]}"
                                )
                            else:
                                st.error(response.json().get("error"))
                        except Exception as e:
                            st.error(f"下载失败")

                    # 删除按钮
                    if cols[6].button("🗑️ 删除", key=f"del_{metadata["name"]}") and download_pass is not None:
                        if len(download_pass) != 4 or not download_pass.isdigit():
                            cols[6].error("需4位数字")
                        else:
                            hashed_dl = hashlib.sha256(download_pass.encode()).hexdigest()
                            try:
                                response = requests.post(
                                    f"{FLASK_CLOUD_PATH}/delete-file",
                                    json={
                                        "token": st.session_state.token,
                                        "filename": metadata["name"],
                                        "password": hashed_dl
                                    }
                                )
                                if response.status_code == 200:
                                    st.rerun()
                                else:
                                    st.error("删除失败")
                            except Exception as e:
                                st.error(f"服务不可用")
                else:
                    cols[4].markdown('-')
                    cols[5].markdown('不可下载')
                    cols[6].markdown('不可删除')

    except Exception as e:
        st.error('服务连接错误')


if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    flask_thread = threading.Thread(
        target=lambda: flask_app.run(host='0.0.0.0', port=FLASK_APP_PORT, threaded=True)
    )
    flask_thread.daemon = True
    flask_thread.start()

    if st.runtime.exists():
        # streamlit命令行启动直接运行
        streamlit_ui()
    else:
        # 非streamlit命令行启动以代码命令形式启动
        from streamlit.web.cli import main
        import sys

        sys.argv = [
            "streamlit", "run", __file__,
            f"--server.port={STREAMLIT_APP_PORT}",
            f"--server.baseUrlPath={STREAMLIT_BASE_PATH}",
            "--server.headless=true"
        ]
        main()
