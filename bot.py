<!DOCTYPE html>
<html lang="bn">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>File Transfer Hub</title>
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
  <style>
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
    }

    body {
      background: radial-gradient(circle at top, #1e1b4b 0%, #0f172a 100%);
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      justify-content: center;
      align-items: center;
      padding: 24px;
    }

    .container {
      width: 100%;
      max-width: 960px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 28px;
    }

    .card {
      background: rgba(30, 41, 59, 0.7);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 20px;
      padding: 28px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
      display: flex;
      flex-direction: column;
      gap: 20px;
    }

    .header {
      display: flex;
      align-items: center;
      gap: 14px;
    }

    .icon-badge {
      width: 46px;
      height: 46px;
      border-radius: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 1.3rem;
    }

    .upload-badge {
      background: rgba(99, 102, 241, 0.18);
      color: #818cf8;
      border: 1px solid rgba(99, 102, 241, 0.3);
    }

    .download-badge {
      background: rgba(16, 185, 129, 0.18);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
    }

    .header-text h3 {
      font-size: 1.2rem;
      font-weight: 600;
      color: #f8fafc;
    }

    .header-text p {
      font-size: 0.85rem;
      color: #94a3b8;
    }

    /* Drop Zone */
    .drop-zone {
      border: 2px dashed rgba(148, 163, 184, 0.3);
      border-radius: 16px;
      padding: 32px 16px;
      text-align: center;
      background: rgba(15, 23, 42, 0.4);
      cursor: pointer;
      transition: all 0.25s ease;
    }

    .drop-zone.dragover {
      border-color: #818cf8;
      background: rgba(99, 102, 241, 0.1);
      transform: scale(0.99);
    }

    .drop-zone i {
      font-size: 2.2rem;
      color: #818cf8;
      margin-bottom: 12px;
      transition: transform 0.2s ease;
    }

    .drop-zone:hover i {
      transform: translateY(-3px);
    }

    .drop-zone span {
      display: block;
      font-size: 0.95rem;
      color: #cbd5e1;
      font-weight: 500;
    }

    .drop-zone small {
      font-size: 0.78rem;
      color: #64748b;
      margin-top: 6px;
      display: block;
    }

    /* File List & Items */
    .file-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .item-card {
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 14px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      transition: border-color 0.2s ease;
    }

    .item-card:hover {
      border-color: rgba(255, 255, 255, 0.15);
    }

    .item-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .file-info {
      display: flex;
      align-items: center;
      gap: 12px;
      overflow: hidden;
    }

    .file-icon {
      font-size: 1.5rem;
      flex-shrink: 0;
    }

    .file-details {
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
    }

    .file-name {
      font-size: 0.9rem;
      font-weight: 500;
      color: #f1f5f9;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .file-size {
      font-size: 0.75rem;
      color: #94a3b8;
      margin-top: 2px;
    }

    /* Progress Bar */
    .progress-bar-bg {
      width: 100%;
      height: 6px;
      background: #334155;
      border-radius: 999px;
      overflow: hidden;
    }

    .progress-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, #6366f1, #a855f7);
      border-radius: 999px;
      transition: width 0.2s ease;
    }

    /* Buttons */
    .btn-action {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      padding: 9px 16px;
      border-radius: 10px;
      font-size: 0.82rem;
      font-weight: 600;
      border: none;
      cursor: pointer;
      flex-shrink: 0;
      transition: all 0.2s ease;
      text-decoration: none;
    }

    .btn-download {
      background: #10b981;
      color: #ffffff;
    }

    .btn-download:hover {
      background: #059669;
      transform: translateY(-1px);
    }

    .status-text {
      font-size: 0.8rem;
      font-weight: 600;
    }
  </style>
</head>
<body>

  <div class="container">
    <!-- আপলোড সেকশন -->
    <div class="card">
      <div class="header">
        <div class="icon-badge upload-badge">
          <i class="fa-solid fa-cloud-arrow-up"></i>
        </div>
        <div class="header-text">
          <h3>ফাইল আপলোড</h3>
          <p>আপনার ফাইল ড্রপ করুন অথবা ব্রাউজ করুন</p>
        </div>
      </div>

      <div class="drop-zone" id="dropZone">
        <i class="fa-solid fa-cloud-arrow-up"></i>
        <span>ক্লিক করে ফাইল বেছে নিন</span>
        <small>ড্র্যাগ অ্যান্ড ড্রপও করতে পারেন</small>
        <input type="file" id="fileInput" hidden multiple />
      </div>

      <div class="file-list" id="uploadList">
        <!-- আপলোড স্ট্যাটাস কার্ড -->
        <div class="item-card" id="demoUploadCard">
          <div class="item-meta">
            <div class="file-info">
              <i class="fa-solid fa-file-zipper file-icon" style="color: #818cf8;"></i>
              <div class="file-details">
                <div class="file-name" id="uploadFileName">application_assets.zip</div>
                <div class="file-size" id="uploadFileSize">২৪.৫ MB • আপলোড হচ্ছে...</div>
              </div>
            </div>
            <span class="status-text" id="uploadPercent" style="color: #818cf8;">৬৫%</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" id="uploadProgressBar" style="width: 65%;"></div>
          </div>
        </div>
      </div>
    </div>

    <!-- ডাউনলোড সেকশন -->
    <div class="card">
      <div class="header">
        <div class="icon-badge download-badge">
          <i class="fa-solid fa-cloud-arrow-down"></i>
        </div>
        <div class="header-text">
          <h3>ফাইল ডাউনলোড</h3>
          <p>প্রস্তুত ফাইলগুলো সংগ্রহ করুন</p>
        </div>
      </div>

      <div class="file-list">
        <!-- রেডি ফাইল ১ -->
        <div class="item-card">
          <div class="item-meta">
            <div class="file-info">
              <i class="fa-solid fa-file-code file-icon" style="color: #38bdf8;"></i>
              <div class="file-details">
                <div class="file-name">source_code_v2.zip</div>
                <div class="file-size">৩৪.২ MB • প্রস্তুত</div>
              </div>
            </div>
            <button class="btn-action btn-download" onclick="startDownload(this, 'source_code_v2.zip')">
              <i class="fa-solid fa-download"></i> ডাউনলোড
            </button>
          </div>
        </div>

        <!-- রেডি ফাইল ২ -->
        <div class="item-card">
          <div class="item-meta">
            <div class="file-info">
              <i class="fa-solid fa-file-video file-icon" style="color: #f43f5e;"></i>
              <div class="file-details">
                <div class="file-name">project_demo_1080p.mp4</div>
                <div class="file-size">১২৪.০ MB • প্রস্তুত</div>
              </div>
            </div>
            <button class="btn-action btn-download" onclick="startDownload(this, 'project_demo_1080p.mp4')">
              <i class="fa-solid fa-download"></i> ডাউনলোড
            </button>
          </div>
        </div>

        <!-- চলমান ডাউনলোড ফাইল -->
        <div class="item-card">
          <div class="item-meta">
            <div class="file-info">
              <i class="fa-solid fa-file-image file-icon" style="color: #f59e0b;"></i>
              <div class="file-details">
                <div class="file-name">banner_graphics_pack.rar</div>
                <div class="file-size">৫২.১ MB • ডাউনলোড চলছে</div>
              </div>
            </div>
            <span class="status-text" style="color: #34d399;">৮৫%</span>
          </div>
          <div class="progress-bar-bg">
            <div class="progress-bar-fill" style="width: 85%; background: linear-gradient(90deg, #10b981, #34d399);"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <script>
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const uploadFileName = document.getElementById('uploadFileName');
    const uploadFileSize = document.getElementById('uploadFileSize');
    const uploadPercent = document.getElementById('uploadPercent');
    const uploadProgressBar = document.getElementById('uploadProgressBar');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', () => {
      dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('dragover');
      if (e.dataTransfer.files.length > 0) {
        handleFileUpload(e.dataTransfer.files[0]);
      }
    });

    fileInput.addEventListener('change', (e) => {
      if (e.target.files.length > 0) {
        handleFileUpload(e.target.files[0]);
      }
    });

    function handleFileUpload(file) {
      uploadFileName.textContent = file.name;
      const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
      uploadFileSize.textContent = `${sizeMB} MB • আপলোড হচ্ছে...`;

      let progress = 0;
      uploadProgressBar.style.width = '0%';
      uploadPercent.textContent = '0%';

      const interval = setInterval(() => {
        progress += 10;
        uploadProgressBar.style.width = progress + '%';
        uploadPercent.textContent = progress + '%';

        if (progress >= 100) {
          clearInterval(interval);
          uploadFileSize.textContent = `${sizeMB} MB • সম্পূর্ণ হয়েছে`;
          uploadPercent.textContent = 'Done';
          uploadPercent.style.color = '#34d399';
        }
      }, 150);
    }

    function startDownload(button, fileName) {
      button.disabled = true;
      button.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> শুরু হচ্ছে...';
      
      setTimeout(() => {
        button.innerHTML = '<i class="fa-solid fa-check"></i> সম্পন্ন';
        button.style.background = '#334155';
      }, 1200);
    }
  </script>
</body>
</html>