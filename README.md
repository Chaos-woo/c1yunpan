# 项目简介
使用Python的Flask和Streamlit库实现的云服务器网盘服务。（Deepseek-R1方案讨论与代码实现）

# 基础架构
```
云服务器
├── Nginx (反向代理)
│   ├── /c1yunpan/api/* → Flask (5000端口)
│   └── /c1yunpan → Streamlit (8501端口)
├── Flask API
│   ├── /c1yunpan/api/token (临时密钥token)
│   ├── /c1yunpan/api/status (网盘状态)
│   ├── /c1yunpan/api/upload (文件上传)
│   ├── /c1yunpan/api/files (文件列表)
│   ├── /c1yunpan/api/download/<filename> (文件下载)
│   ├── /c1yunpan/api/download-by-pass (文件直接下载)
│   └── /c1yunpan/api/delete-file (文件删除)
│   ├── 文件生命周期管理
│       ├── 定时清理任务
└── Streamlit UI
    ├── 密码验证
    ├── 文件列表展示
    ├── 文件上传表单
    └── 文件下载验证
    ├── 文件生命周期管理
    │   ├── 过期时间设置
    │   └── 剩余时间展示
    └── 存储配额管理
        ├── 文件大小限制
        ├── 存储空间计算
        └── 配额状态展示
```

# 环境准备
1. `app.py` 同级创建 `./cloud_disk/uploads` 文件夹和 `./cloud_disk/metadata.txt` 文件
2. 创建Python虚拟环境，安装必要三方库

# 运行
运行 `app.py` 脚本文件

# 效果
![首页](https://github.com/Chaos-woo/c1yunpan/blob/main/home.png)
![文件上传](https://github.com/Chaos-woo/c1yunpan/blob/main/upload_file.png)
![文件列表](https://github.com/Chaos-woo/c1yunpan/blob/main/file_list.png)

