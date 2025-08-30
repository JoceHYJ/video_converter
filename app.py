from flask import Flask, render_template, request, jsonify, send_from_directory
import os
import subprocess
import uuid
import shutil
import pathlib

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 增大到50MB以支持更大视频
app.config['UPLOAD_FOLDER'] = 'tmp'

# 确保临时目录存在
if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# 存储处理进度的字典
processing_progress = {}

def clean_temp_files():
    """清理临时目录，避免占用过多空间"""
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        for filename in os.listdir(app.config['UPLOAD_FOLDER']):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                app.logger.error(f"清理临时文件失败: {e}")

def get_file_extension(file_path):
    """获取文件扩展名"""
    return pathlib.Path(file_path).suffix

def download_video(url, output_dir, custom_filename=None, task_id=None):
    """使用you-get下载视频，支持自定义文件名和进度更新"""
    try:
        # 生成唯一的文件名前缀
        unique_id = str(uuid.uuid4())[:8]
        base_name = custom_filename if custom_filename else f"video_{unique_id}"
        output_path = os.path.join(output_dir, base_name)
        
        # 更新进度
        if task_id:
            processing_progress[task_id] = {"percent": 10, "message": "开始下载视频..."}
        
        # 使用you-get下载视频
        result = subprocess.run(
            ['you-get', '-o', output_dir, '-O', base_name, url],
            capture_output=True,
            text=True
        )
        
        # 更新进度
        if task_id:
            processing_progress[task_id] = {"percent": 40, "message": "视频下载完成，准备转换音频..."}
        
        if result.returncode != 0:
            return None, None, f"下载失败: {result.stderr}"
        
        # 查找下载的视频文件
        for file in os.listdir(output_dir):
            if file.startswith(base_name):
                video_path = os.path.join(output_dir, file)
                video_ext = get_file_extension(video_path)
                return video_path, video_ext, None
        
        return None, None, "下载成功，但未找到视频文件"
    except Exception as e:
        return None, None, f"下载过程出错: {str(e)}"

def convert_to_audio(video_path, output_dir, format='mp3', quality=4, custom_filename=None, task_id=None):
    """使用ffmpeg将视频转换为音频，支持自定义文件名和进度更新"""
    try:
        # 生成输出文件名
        base_name = custom_filename if custom_filename else f"{os.path.splitext(os.path.basename(video_path))[0]}_audio"
        output_file = f"{base_name}.{format}"
        output_path = os.path.join(output_dir, output_file)
        
        # 更新进度
        if task_id:
            processing_progress[task_id] = {"percent": 60, "message": "开始转换音频..."}
        
        # 根据格式设置ffmpeg参数
        if format == 'mp3':
            codec = 'libmp3lame'
            quality_param = f'-q:a {quality}'
        elif format == 'wav':
            codec = 'pcm_s16le'
            quality_param = '-b:a 1411k'  # WAV固定比特率
        elif format == 'm4a':
            codec = 'aac'
            quality_param = f'-b:a {128 + quality*32}k'  # 128k到448k
        elif format == 'flac':
            codec = 'flac'
            quality_param = f'-compression_level {quality}'  # 0-8
        else:
            return None, f"不支持的格式: {format}"
        
        # 构建ffmpeg命令
        cmd = f'ffmpeg -i "{video_path}" -vn -acodec {codec} {quality_param} "{output_path}"'
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        
        # 更新进度
        if task_id:
            processing_progress[task_id] = {"percent": 90, "message": "音频转换完成..."}
        
        if result.returncode != 0:
            return None, f"转换失败: {result.stderr}"
        
        return output_path, None
    except Exception as e:
        return None, f"转换过程出错: {str(e)}"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert():
    try:
        data = request.get_json()
        url = data.get('url')
        format = data.get('format', 'mp3')
        quality = int(data.get('quality', 4))
        video_filename = data.get('videoFileName', '')
        audio_filename = data.get('audioFileName', '')
        
        # 生成任务ID用于跟踪进度
        task_id = str(uuid.uuid4())
        processing_progress[task_id] = {"percent": 0, "message": "准备开始处理..."}
        
        if not url:
            return jsonify({'success': False, 'error': '请提供视频URL'})
        
        # 清理旧的临时文件
        clean_temp_files()
        
        # 下载视频
        video_path, video_ext, error = download_video(
            url, 
            app.config['UPLOAD_FOLDER'],
            video_filename,
            task_id
        )
        if error:
            return jsonify({'success': False, 'error': error})
        
        # 转换为音频
        audio_path, error = convert_to_audio(
            video_path, 
            app.config['UPLOAD_FOLDER'], 
            format, 
            quality,
            audio_filename,
            task_id
        )
        if error:
            return jsonify({'success': False, 'error': error})
        
        # 生成下载链接
        video_filename = os.path.basename(video_path)
        audio_filename = os.path.basename(audio_path)
        
        # 完成进度
        processing_progress[task_id] = {"percent": 100, "message": "处理完成！"}
        
        return jsonify({
            'success': True,
            'video_url': f'/download/{video_filename}',
            'audio_url': f'/download/{audio_filename}',
            'video_ext': video_ext
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/download/<filename>')
def download_file(filename):
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/progress/<task_id>')
def get_progress(task_id):
    """获取处理进度的API端点"""
    progress = processing_progress.get(task_id, {"percent": 0, "message": "准备中..."})
    return jsonify(progress)

if __name__ == '__main__':
    app.run(debug=True)
    