/**
 * Social Recipes UI - Main JavaScript
 * Supports multiple concurrent jobs with progress persistence
 */

document.addEventListener('DOMContentLoaded', function() {
    // DOM Elements - declared early so they're available for checkForSharedContent
    const videoUrlInput = document.getElementById('video-url');
    
    // Check for shared URL from Web Share Target API
    checkForSharedContent();
    
    // Initialize Socket.IO connection
    const socket = io();
    
    // Job state management
    const activeJobs = new Map();  // job_id -> job data
    const processBtn = document.getElementById('process-btn');
    const jobsSection = document.getElementById('jobs-section');
    const jobsList = document.getElementById('jobs-list');
    const jobCount = document.getElementById('job-count');
    const resultSection = document.getElementById('result-section');
    const completedRecipes = document.getElementById('completed-recipes');
    
    // Templates
    const jobCardTemplate = document.getElementById('job-card-template');
    const completedCardTemplate = document.getElementById('completed-card-template');
    
    // Preview Modal Elements
    const previewModal = document.getElementById('preview-modal');
    const previewTarget = document.getElementById('preview-target');
    const previewImageContainer = document.getElementById('preview-image-container');
    const previewImage = document.getElementById('preview-image');
    const imageCandidatesGrid = document.getElementById('image-candidates-grid');
    const previewTitle = document.getElementById('preview-title');
    const previewDescription = document.getElementById('preview-description');
    const previewIngredients = document.getElementById('preview-ingredients');
    const previewInstructions = document.getElementById('preview-instructions');
    const confirmUploadBtn = document.getElementById('confirm-upload-btn');
    const cancelUploadBtn = document.getElementById('cancel-upload-btn');
    
    // Current preview state
    let currentUploadId = null;
    let currentPreviewJobId = null;
    let selectedImageIndex = 0;
    
    // ===== Socket.IO Event Handlers =====
    
    socket.on('connect', function() {
        console.log('Connected to server');
        // Restore active jobs on reconnect
        restoreActiveJobs();
    });
    
    socket.on('disconnect', function() {
        console.log('Disconnected from server');
    });
    
    socket.on('job_progress', function(data) {
        updateJobProgress(data.job_id, data.stage, data.message, data.percent, data.video_title);
    });
    
    socket.on('job_complete', function(data) {
        handleJobComplete(data.job_id, data.recipe);
    });
    
    socket.on('job_failed', function(data) {
        handleJobFailed(data.job_id, data.error);
    });
    
    socket.on('job_cancelled', function(data) {
        handleJobCancelled(data.job_id);
    });
    
    socket.on('recipe_preview', function(data) {
        showPreviewModal(data);
    });
    
    socket.on('recipe_cancelled', function(data) {
        hidePreviewModal();
        showNotification(data.message || 'Upload cancelled', 'info');
    });
    
    // Legacy progress event (for backward compatibility)
    socket.on('progress', function(data) {
        // Find the first active job and update it (legacy single-job mode)
        if (activeJobs.size === 1) {
            const jobId = activeJobs.keys().next().value;
            updateJobProgress(jobId, data.stage, data.message, data.percent);
        }
    });
    
    // ===== Event Listeners =====
    
    if (processBtn) {
        processBtn.addEventListener('click', startNewJob);
    }
    
    if (videoUrlInput) {
        videoUrlInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                startNewJob();
            }
        });
    }
    
    if (confirmUploadBtn) {
        confirmUploadBtn.addEventListener('click', function() {
            if (currentUploadId) {
                socket.emit('confirm_upload', {
                    upload_id: currentUploadId,
                    selected_image_index: selectedImageIndex
                });
                hidePreviewModal();
            }
        });
    }
    
    if (cancelUploadBtn) {
        cancelUploadBtn.addEventListener('click', function() {
            if (currentUploadId) {
                socket.emit('cancel_upload', { upload_id: currentUploadId });
                hidePreviewModal();
            }
        });
    }
    
    // ===== Job Management Functions =====
    
    /**
     * Start a new analysis job
     */
    async function startNewJob() {
        const url = videoUrlInput.value.trim();
        
        if (!url) {
            showNotification('Please enter a video URL', 'error');
            videoUrlInput.focus();
            return;
        }
        
        if (!isValidUrl(url)) {
            showNotification('Please enter a valid URL', 'error');
            videoUrlInput.focus();
            return;
        }
        
        // Check if we already have 3 active jobs
        if (activeJobs.size >= 3) {
            showNotification('Maximum 3 concurrent jobs allowed. Please wait for one to complete.', 'warning');
            return;
        }
        
        try {
            const response = await fetch('/api/jobs', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ url: url })
            });
            
            const data = await response.json();
            
            if (data.error) {
                showNotification(data.error, 'error');
                return;
            }
            
            // Add job to active jobs
            const jobId = data.job_id;
            activeJobs.set(jobId, {
                id: jobId,
                url: url,
                status: 'pending',
                progress: 0,
                stage: 'pending',
                message: 'Starting...',
                video_title: null
            });
            
            // Subscribe to job updates
            socket.emit('subscribe_job', { job_id: jobId });
            
            // Create job card
            createJobCard(jobId, url);
            
            // Clear input
            videoUrlInput.value = '';
            
            // Show jobs section
            updateJobsDisplay();
            
            showNotification('Job started!', 'success');
            
        } catch (error) {
            console.error('Error starting job:', error);
            showNotification('Failed to start job', 'error');
        }
    }
    
    /**
     * Restore active jobs from server on page load/reconnect
     */
    async function restoreActiveJobs() {
        try {
            const response = await fetch('/api/jobs');
            const data = await response.json();
            
            if (data.jobs && data.jobs.length > 0) {
                data.jobs.forEach(job => {
                    // Skip if we already have this job
                    if (activeJobs.has(job.id)) {
                        // Just update it
                        updateJobProgress(job.id, job.current_stage, job.stage_message, job.progress, job.video_title);
                        return;
                    }
                    
                    // Add to active jobs
                    activeJobs.set(job.id, {
                        id: job.id,
                        url: job.url,
                        status: job.status,
                        progress: job.progress,
                        stage: job.current_stage,
                        message: job.stage_message,
                        video_title: job.video_title
                    });
                    
                    // Subscribe to job updates
                    socket.emit('subscribe_job', { job_id: job.id });
                    
                    // Create job card if it doesn't exist
                    if (!document.querySelector(`.job-card[data-job-id="${job.id}"]`)) {
                        createJobCard(job.id, job.url);
                        updateJobCardUI(job.id, job.current_stage, job.stage_message, job.progress, job.video_title);
                    }
                });
                
                updateJobsDisplay();
            }
        } catch (error) {
            console.error('Error restoring jobs:', error);
        }
    }
    
    /**
     * Create a job card in the UI
     */
    function createJobCard(jobId, url) {
        const template = jobCardTemplate.content.cloneNode(true);
        const card = template.querySelector('.job-card');
        
        card.dataset.jobId = jobId;
        card.querySelector('.job-url').textContent = truncateUrl(url);
        
        // Add cancel button handler
        card.querySelector('.cancel-job-btn').addEventListener('click', function() {
            cancelJob(jobId);
        });
        
        jobsList.appendChild(card);
    }
    
    /**
     * Update job progress
     */
    function updateJobProgress(jobId, stage, message, percent, videoTitle) {
        // Update state
        const job = activeJobs.get(jobId);
        if (job) {
            job.stage = stage;
            job.message = message;
            job.progress = percent;
            if (videoTitle) {
                job.video_title = videoTitle;
            }
        }
        
        // Update UI
        updateJobCardUI(jobId, stage, message, percent, videoTitle);
    }
    
    /**
     * Update job card UI
     */
    function updateJobCardUI(jobId, stage, message, percent, videoTitle) {
        const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
        if (!card) return;
        
        // Update title
        if (videoTitle) {
            card.querySelector('.job-title').textContent = videoTitle;
        }
        
        // Update progress bar
        card.querySelector('.progress-bar').style.width = percent + '%';
        card.querySelector('.progress-text').textContent = percent + '%';
        
        // Update message
        card.querySelector('.message-text').textContent = message;
        
        // Update step indicators
        const stages = ['info', 'download', 'transcribe', 'visual', 'image', 'evaluate', 'upload'];
        let displayStage = stage;
        if (stage === 'preview') displayStage = 'upload';
        
        const currentIndex = stages.indexOf(displayStage);
        
        stages.forEach((s, index) => {
            const step = card.querySelector(`.job-step[data-stage="${s}"]`);
            if (!step) return;
            
            step.classList.remove('active', 'completed', 'error');
            
            if (stage === 'error') {
                if (index <= currentIndex) step.classList.add('error');
            } else if (stage === 'complete') {
                step.classList.add('completed');
            } else if (index < currentIndex) {
                step.classList.add('completed');
            } else if (index === currentIndex) {
                step.classList.add('active');
            }
        });
        
        // Update card state
        card.classList.remove('pending', 'processing', 'completed', 'error');
        if (stage === 'complete') {
            card.classList.add('completed');
        } else if (stage === 'error') {
            card.classList.add('error');
        } else {
            card.classList.add('processing');
        }
    }
    
    /**
     * Handle job completion
     */
    function handleJobComplete(jobId, recipe) {
        const job = activeJobs.get(jobId);
        
        // Update card to completed state
        const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
        if (card) {
            card.classList.add('completed');
            card.querySelector('.cancel-job-btn').style.display = 'none';
            
            // Auto-remove after a few seconds
            setTimeout(() => {
                card.classList.add('fade-out');
                setTimeout(() => {
                    card.remove();
                    activeJobs.delete(jobId);
                    updateJobsDisplay();
                }, 300);
            }, 3000);
        }
        
        // Add to completed section
        addCompletedCard(jobId, recipe);
        
        showNotification(`Recipe "${recipe.name || 'Untitled'}" created successfully!`, 'success');
    }
    
    /**
     * Handle job failure
     */
    function handleJobFailed(jobId, error) {
        const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
        if (card) {
            card.classList.add('error');
            card.querySelector('.message-text').textContent = error;
            card.querySelector('.cancel-job-btn').innerHTML = '<i class="fas fa-trash"></i>';
            card.querySelector('.cancel-job-btn').title = 'Remove';
            
            // Change cancel to remove
            card.querySelector('.cancel-job-btn').onclick = function() {
                card.remove();
                activeJobs.delete(jobId);
                updateJobsDisplay();
            };
        }
        
        showNotification(`Job failed: ${error}`, 'error');
    }
    
    /**
     * Handle job cancellation
     */
    function handleJobCancelled(jobId) {
        const card = document.querySelector(`.job-card[data-job-id="${jobId}"]`);
        if (card) {
            card.classList.add('fade-out');
            setTimeout(() => {
                card.remove();
                activeJobs.delete(jobId);
                updateJobsDisplay();
            }, 300);
        }
        
        showNotification('Job cancelled', 'info');
    }
    
    /**
     * Cancel a running job
     */
    async function cancelJob(jobId) {
        try {
            const response = await fetch(`/api/jobs/${jobId}`, {
                method: 'DELETE'
            });
            
            const data = await response.json();
            
            if (data.error) {
                showNotification(data.error, 'error');
                return;
            }
            
            // Job cancelled - will be handled by socket event
        } catch (error) {
            console.error('Error cancelling job:', error);
            showNotification('Failed to cancel job', 'error');
        }
    }
    
    /**
     * Add completed recipe card
     */
    function addCompletedCard(jobId, recipe) {
        const template = completedCardTemplate.content.cloneNode(true);
        const card = template.querySelector('.completed-card');
        
        card.dataset.jobId = jobId;
        card.querySelector('.completed-title').textContent = recipe.name || 'Untitled Recipe';
        card.querySelector('.view-history-btn').href = '/history';
        
        completedRecipes.insertBefore(card, completedRecipes.firstChild);
        
        // Show result section
        resultSection.style.display = 'block';
        
        // Keep only last 5 completed cards
        const cards = completedRecipes.querySelectorAll('.completed-card');
        if (cards.length > 5) {
            cards[cards.length - 1].remove();
        }
    }
    
    /**
     * Update jobs display visibility
     */
    function updateJobsDisplay() {
        const count = activeJobs.size;
        jobCount.textContent = `(${count})`;
        
        if (count > 0) {
            jobsSection.style.display = 'block';
        } else {
            jobsSection.style.display = 'none';
        }
    }
    
    // ===== Preview Modal Functions =====
    
    function showPreviewModal(data) {
        if (!previewModal) return;
        
        currentUploadId = data.upload_id;
        currentPreviewJobId = data.job_id;
        selectedImageIndex = data.best_image_index || 0;
        
        // Set target name (with dual export badge if needed)
        if (previewTarget) {
            if (data.export_to_both) {
                previewTarget.innerHTML = '<span class="dual-export-badge"><i class="fas fa-sync-alt"></i> Both</span> Tandoor & Mealie';
            } else {
                previewTarget.textContent = data.output_target || 'recipe manager';
            }
        }
        
        // Build image candidates grid
        if (imageCandidatesGrid && data.candidate_images && data.candidate_images.length > 0) {
            imageCandidatesGrid.innerHTML = '';
            imageCandidatesGrid.style.display = 'grid';
            
            data.candidate_images.forEach((candidate, idx) => {
                const imgWrapper = document.createElement('div');
                imgWrapper.className = 'image-candidate' + (candidate.is_best ? ' is-best' : '') + (idx === selectedImageIndex ? ' selected' : '');
                imgWrapper.dataset.index = candidate.index;
                
                const img = document.createElement('img');
                img.src = 'data:image/jpeg;base64,' + candidate.data;
                img.alt = 'Dish candidate ' + (candidate.index + 1);
                
                if (candidate.is_best) {
                    const badge = document.createElement('span');
                    badge.className = 'ai-badge';
                    badge.innerHTML = '<i class="fas fa-star"></i> AI Pick';
                    imgWrapper.appendChild(badge);
                }
                
                imgWrapper.appendChild(img);
                
                imgWrapper.addEventListener('click', function() {
                    selectImage(candidate.index, candidate.data);
                });
                
                imageCandidatesGrid.appendChild(imgWrapper);
            });
            
            if (data.image_data && previewImage && previewImageContainer) {
                previewImage.src = 'data:image/jpeg;base64,' + data.image_data;
                previewImageContainer.style.display = 'block';
            }
        } else {
            if (imageCandidatesGrid) imageCandidatesGrid.style.display = 'none';
            if (data.image_data && previewImage && previewImageContainer) {
                previewImage.src = 'data:image/jpeg;base64,' + data.image_data;
                previewImageContainer.style.display = 'block';
            } else if (previewImageContainer) {
                previewImageContainer.style.display = 'none';
            }
        }
        
        // Set recipe info
        if (previewTitle) previewTitle.textContent = data.recipe.name || 'Untitled Recipe';
        if (previewDescription) previewDescription.textContent = data.recipe.description || '';
        
        if (previewIngredients) {
            previewIngredients.innerHTML = '';
            if (data.recipe.recipeIngredient) {
                data.recipe.recipeIngredient.forEach(ing => {
                    const li = document.createElement('li');
                    li.textContent = ing;
                    previewIngredients.appendChild(li);
                });
            }
        }
        
        if (previewInstructions) {
            previewInstructions.innerHTML = '';
            if (data.recipe.recipeInstructions) {
                data.recipe.recipeInstructions.forEach(inst => {
                    const li = document.createElement('li');
                    li.textContent = typeof inst === 'object' ? inst.text : inst;
                    previewInstructions.appendChild(li);
                });
            }
        }
        
        previewModal.style.display = 'flex';
        document.body.style.overflow = 'hidden';
    }
    
    function selectImage(index, imageData) {
        selectedImageIndex = index;
        
        if (previewImage && imageData) {
            previewImage.src = 'data:image/jpeg;base64,' + imageData;
        }
        
        if (imageCandidatesGrid) {
            imageCandidatesGrid.querySelectorAll('.image-candidate').forEach(candidate => {
                if (parseInt(candidate.dataset.index) === index) {
                    candidate.classList.add('selected');
                } else {
                    candidate.classList.remove('selected');
                }
            });
        }
    }
    
    function hidePreviewModal() {
        if (previewModal) {
            previewModal.style.display = 'none';
            document.body.style.overflow = '';
        }
        currentUploadId = null;
        currentPreviewJobId = null;
    }
    
    // ===== Utility Functions =====
    
    function checkForSharedContent() {
        const urlParams = new URLSearchParams(window.location.search);
        const sharedUrl = urlParams.get('url') || urlParams.get('text');
        
        if (sharedUrl && videoUrlInput) {
            window.history.replaceState({}, document.title, window.location.pathname);
            videoUrlInput.value = sharedUrl;
            videoUrlInput.focus();
            showNotification('URL received! Click "Extract Recipe" to continue.', 'success');
        }
    }
    
    function isValidUrl(string) {
        try {
            new URL(string);
            return true;
        } catch (_) {
            return false;
        }
    }
    
    function truncateUrl(url) {
        if (!url) return '';
        try {
            const parsed = new URL(url);
            return parsed.hostname + parsed.pathname.slice(0, 20) + (parsed.pathname.length > 20 ? '...' : '');
        } catch {
            return url.slice(0, 40) + (url.length > 40 ? '...' : '');
        }
    }
    
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    function showNotification(message, type) {
        const flashContainer = document.querySelector('.flash-messages') || createFlashContainer();
        
        const flashMessage = document.createElement('div');
        flashMessage.className = 'flash-message flash-' + type;
        flashMessage.innerHTML = `
            ${escapeHtml(message)}
            <button class="flash-close" onclick="this.parentElement.remove()">&times;</button>
        `;
        
        flashContainer.appendChild(flashMessage);
        
        setTimeout(() => {
            if (flashMessage.parentElement) {
                flashMessage.remove();
            }
        }, 5000);
    }
    
    function createFlashContainer() {
        const container = document.createElement('div');
        container.className = 'flash-messages';
        document.body.appendChild(container);
        return container;
    }
    
    // Initialize - restore active jobs on page load
    restoreActiveJobs();
});
