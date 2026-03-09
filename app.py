import streamlit as st
import os
import db
import douyin_slice
import dotenv
import subprocess
import shutil

# 确保数据库初始化
db.init_db()

# 初始化 session state 来防止按钮点击时状态丢失
if 'last_merged_video' not in st.session_state:
    st.session_state.last_merged_video = None
if 'last_output_dir' not in st.session_state:
    st.session_state.last_output_dir = None

st.set_page_config(page_title="极速 AI 切片机", page_icon="✂️", layout="wide")

# ===== 侧边栏 =====
st.sidebar.title("⚙️ 全局设置")
st.sidebar.markdown('---')

api_key = st.sidebar.text_input("🔑 AI 密钥设置", value=os.getenv("DASHSCOPE_API_KEY", ""), type="password", help="小白请注意：在这里填入你在阿里云百炼申请到的 API Key 即可使用！代码层面完全不用修改。")
if api_key and api_key != os.getenv("DASHSCOPE_API_KEY", ""):
    dotenv.set_key(".env", "DASHSCOPE_API_KEY", api_key)
    os.environ["DASHSCOPE_API_KEY"] = api_key

orientation = st.sidebar.radio(
    "最终出片横竖选项：", 
    ["portrait", "landscape"], 
    format_func=lambda x: "📱 竖屏模板 (9:16, 默认)" if x == "portrait" else "🖥️ 横屏模板 (16:9)"
)

# ===== 顶部 Tabs =====
tab1, tab2 = st.tabs(["🚀 切片成片直通车", "🗂️ 本地最终资产库"])

# ----- Tab 1: 主工作台 -----
with tab1:
    st.title("🔥 智能一键切片机")
    st.caption("你只需要丢入一个抖音链接，全程“傻瓜式”自动处理，中间产生的零碎素材会自动焚毁，最终只会交还给你一部完美的成品合集。")
    
    share_text = st.text_area("请在这里粘贴分享内容或抖音链接：", height=100, placeholder="例如：https://v.douyin.com/...")
    
    if st.button("✨ 立即开始处理 ✨", type="primary", use_container_width=True):
        if not share_text.strip():
            st.warning("⚠️ 请输入有效的视频分享链接！")
        elif not os.getenv("DASHSCOPE_API_KEY"):
            st.error("❌ 无法启动：请先在界面左侧【全局设置】里面填上你的 API 密钥！")
        else:
            # 清理历史状态
            st.session_state.last_merged_video = None
            st.session_state.last_output_dir = None
            
            with st.status("🚀 正在分配系统计算资源并启动智能流水线...", expanded=True) as status:
                has_error = False
                
                # 接收来自底层的通俗口语化过程信息，不含专业技术词汇
                for msg in douyin_slice.process_video_gen(share_text.strip(), orientation=orientation):
                    if msg.startswith("❌"):
                        status.update(label=msg, state="error", expanded=True)
                        st.error(msg)
                        has_error = True
                    elif msg.startswith("🎯"):
                        parts = msg.split("|")
                        status.update(label="✅ 当前任务所有加工流处理完毕！", state="complete", expanded=False)
                        st.success(parts[0])
                        if len(parts) >= 4:
                            st.session_state.last_merged_video = parts[1]
                            st.session_state.last_output_dir = parts[3]
                    else:
                        st.write(msg)
                        status.update(label=msg)
            
    if st.session_state.last_merged_video:
        st.divider()
        st.subheader("📺 你的本期爆款成片")
        col1, col2 = st.columns([1, 1])
        with col1:
            if os.path.exists(st.session_state.last_merged_video):
                st.video(st.session_state.last_merged_video)
        with col2:
            st.success("🎉 最终产出文件已稳定保存，原视频和碎分片音频已被彻底从磁盘自动删除清理。")
            if st.session_state.last_output_dir and os.path.exists(st.session_state.last_output_dir):
                if st.button("📂 【快捷通道】在电脑中直接打开提取结果文件夹", type="primary"):
                    subprocess.Popen(f'explorer "{os.path.abspath(st.session_state.last_output_dir)}"')

# ----- Tab 2: 资产库 -----
with tab2:
    st.title("🗂️ 最终成品输出空间")
    st.caption("这里只存放完全加工好交付给你的最终长合并视频，界面干净整洁，不再有眼花缭乱的碎切片。")
    st.divider()
    
    tasks = db.get_successful_tasks()
    
    if not tasks:
        st.info("👻 空空如也，这里还在静静等待你的第一条爆款入库...")
    else:
        st.success(f"📦 当前本地库中共珍藏了 **{len(tasks)}** 个合并好的整视频。")
        
        cols = st.columns(3)
        for idx, task in enumerate(tasks):
            with cols[idx % 3]:
                with st.container(border=True):
                    st.subheader(f"🎬 任务 {task['id']}")
                    st.caption(f"🗓️ 提取时间：{task['created_at']}")
                    
                    video_p = task.get('video_path')
                    output_folder = None
                    if video_p and os.path.exists(video_p):
                        st.video(video_p)
                        # 一键直达文件所在系统文件夹
                        output_folder = os.path.dirname(os.path.abspath(video_p))
                        if st.button("📂 进入所在文件夹", key=f"open_dir_{task['id']}"):
                            subprocess.Popen(f'explorer "{output_folder}"')
                    else:
                        st.warning("⚠️ 此成片由于被手动挪动或磁盘清理已被移除")
                        
                    st.caption(f"🔗 源追溯片段: {task['url'][:30]}...")
                    
                    if st.button("🗑️ 删除此任务", key=f"del_{task['id']}", type="secondary", use_container_width=True):
                        # 删除物理文件
                        if output_folder and os.path.exists(output_folder):
                            try:
                                shutil.rmtree(output_folder)
                            except Exception:
                                pass
                        # 删除数据库记录
                        db.delete_task(task['id'])
                        st.rerun()
