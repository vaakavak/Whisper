document.addEventListener('DOMContentLoaded', function () {
    // --- DOM Element References ---
    const uploadForm = document.getElementById('upload-form');
    const dropZone = document.getElementById('drop-zone');
    const audioFileInput = document.getElementById('audio-file');
    const fileNameDisplay = document.getElementById('file-name');
    const submitBtn = document.getElementById('submit-btn');
    const clearBtn = document.getElementById('clear-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const progressStatus = document.getElementById('progress-status');
    const resultsContainer = document.getElementById('results-container');
    const processAnotherBtn = document.getElementById('process-another-btn');
    const saveBtns = document.querySelectorAll('.save-btn');

    let currentTaskId = null;
    let progressInterval = null;

    // --- Drag and Drop Logic ---
    dropZone.addEventListener('click', () => audioFileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('dragover');
    });

    dropZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('dragover');
        const files = e.dataTransfer.files;
        if (files.length > 0) {
            audioFileInput.files = files;
            updateFileName();
        }
    });

    audioFileInput.addEventListener('change', updateFileName);

    function updateFileName() {
        if (audioFileInput.files.length > 0) {
            fileNameDisplay.textContent = `Выбранный файл: ${audioFileInput.files[0].name}`;
        } else {
            fileNameDisplay.textContent = '';
        }
    }

    // --- Form Submission ---
    uploadForm.addEventListener('submit', async function (e) {
        e.preventDefault();
        if (!audioFileInput.files[0]) {
            alert('Пожалуйста, выберите аудиофайл.');
            return;
        }

        const formData = new FormData(this);
        resetUI(true); // Reset for new submission

        try {
            const response = await fetch('/upload', {
                method: 'POST',
                body: formData,
            });

            if (!response.ok) {
                throw new Error(`Ошибка сервера: ${response.statusText}`);
            }

            const data = await response.json();
            if (data.task_id) {
                currentTaskId = data.task_id;
                startProgressPolling(currentTaskId);
            } else {
                throw new Error('Не удалось получить ID задачи.');
            }
        } catch (error) {
            console.error('Ошибка при загрузке:', error);
            alert(`Произошла ошибка при загрузке файла. ${error.message}`);
            resetUI(false);
        }
    });

    // --- Progress Polling ---
    function startProgressPolling(taskId) {
        progressInterval = setInterval(async () => {
            try {
                const response = await fetch(`/status/${taskId}`);
                const data = await response.json();

                // Update progress bar
                const percent = data.progress || 0;
                progressBar.style.width = `${percent}%`;
                progressBar.textContent = `${percent}%`;
                progressBar.setAttribute('aria-valuenow', percent);
                progressStatus.textContent = data.status || 'Пожалуйста, подождите...';

                if (data.state === 'SUCCESS') {
                    clearInterval(progressInterval);
                    progressStatus.textContent = 'Обработка завершена!';
                    fetchResults(taskId);
                } else if (data.state === 'FAILURE' || data.state === 'REVOKED') {
                    clearInterval(progressInterval);
                    progressStatus.textContent = `Задача не удалась или была отменена.`;
                    showForm();
                }
            } catch (error) {
                console.error('Ошибка при опросе статуса:', error);
                clearInterval(progressInterval);
                alert('Не удалось получить статус задачи.');
                resetUI(false);
            }
        }, 2000); // Poll every 2 seconds
    }

    // --- Fetch and Display Results ---
    async function fetchResults(taskId) {
        try {
            const response = await fetch(`/result/${taskId}`);
            const data = await response.json();

            if (data.error) {
                throw new Error(data.error);
            }

            document.querySelector('#transcription textarea').value = data.transcription || 'Нет данных.';
            document.querySelector('#timestamps textarea').value = data.timestamps || 'Нет данных.';
            document.querySelector('#summary textarea').value = data.summary || 'Нет данных.';

            progressContainer.classList.add('d-none');
            resultsContainer.classList.remove('d-none');
        } catch (error) {
            console.error('Ошибка при получении результатов:', error);
            alert(`Не удалось загрузить результаты: ${error.message}`);
            resetUI(false);
        }
    }

    // --- UI State Management ---
    function resetUI(isProcessing) {
        // Hide results, show form
        resultsContainer.classList.add('d-none');
        uploadForm.classList.remove('d-none');

        if (isProcessing) {
            // Show progress bar, disable form
            uploadForm.classList.add('d-none');
            progressContainer.classList.remove('d-none');
            progressBar.style.width = '0%';
            progressBar.textContent = '0%';
            progressStatus.textContent = 'Инициализация...';
            submitBtn.classList.add('d-none');
            clearBtn.classList.add('d-none');
            cancelBtn.classList.remove('d-none');
        } else {
            // Full reset to initial state
            progressContainer.classList.add('d-none');
            uploadForm.reset();
            fileNameDisplay.textContent = '';
            submitBtn.classList.remove('d-none');
            clearBtn.classList.remove('d-none');
            cancelBtn.classList.add('d-none');
            if (progressInterval) clearInterval(progressInterval);
        }
    }

    function showForm() {
        uploadForm.classList.remove('d-none');
        submitBtn.classList.remove('d-none');
        clearBtn.classList.remove('d-none');
        cancelBtn.classList.add('d-none');
        progressContainer.classList.add('d-none');
        resultsContainer.classList.add('d-none');
    }

    // --- Button Event Listeners ---
    clearBtn.addEventListener('click', () => {
        resetUI(false);
    });

    processAnotherBtn.addEventListener('click', () => {
        resetUI(false);
    });

    cancelBtn.addEventListener('click', async () => {
        if (!currentTaskId) return;
        try {
            await fetch(`/cancel/${currentTaskId}`, { method: 'POST' });
            clearInterval(progressInterval);
            progressStatus.textContent = 'Отмена задачи...';
            alert('Задача отменена.');
            resetUI(false);
        } catch (error) {
            console.error('Ошибка при отмене задачи:', error);
            alert('Не удалось отменить задачу.');
        }
    });

    saveBtns.forEach(btn => {
        btn.addEventListener('click', function() {
            const targetId = this.getAttribute('data-target');
            const content = document.querySelector(`#${targetId} textarea`).value;
            const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `${targetId}_result.txt`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });
    });
});
