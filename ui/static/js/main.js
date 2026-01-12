/**
 * Social Recipes UI - Main JavaScript
 */

document.addEventListener('DOMContentLoaded', function() {
    // Initialize Socket.IO connection
    const socket = io();
    
    // DOM Elements
    const videoUrlInput = document.getElementById('video-url');
    const processBtn = document.getElementById('process-btn');
    const progressSection = document.getElementById('progress-section');
    const resultSection = document.getElementById('result-section');
    const progressBar = document.getElementById('progress-bar');
    const progressText = document.getElementById('progress-text');
    const logOutput = document.getElementById('log-output');
    const recipePreview = document.getElementById('recipe-preview');
    const newRecipeBtn = document.getElementById('new-recipe-btn');
    
    // Track current processing state
    let isProcessing = false;
    
    // Socket.IO event handlers
    socket.on('connect', function() {
        console.log('Connected to server');
        addLog('info', 'Connected to server');
    });
    
    socket.on('disconnect', function() {
        console.log('Disconnected from server');
        addLog('warning', 'Disconnected from server');
    });
    
    socket.on('progress', function(data) {
        updateProgress(data.stage, data.message, data.percent);
    });
    
    socket.on('recipe_complete', function(data) {
        displayRecipe(data.recipe);
    });
    
    // Process button click handler
    if (processBtn) {
        processBtn.addEventListener('click', startProcessing);
    }
    
    // Allow Enter key to start processing
    if (videoUrlInput) {
        videoUrlInput.addEventListener('keypress', function(e) {
            if (e.key === 'Enter' && !isProcessing) {
                startProcessing();
            }
        });
    }
    
    // New recipe button click handler
    if (newRecipeBtn) {
        newRecipeBtn.addEventListener('click', resetUI);
    }
    
    /**
     * Start video processing
     */
    function startProcessing() {
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
        
        isProcessing = true;
        processBtn.disabled = true;
        processBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Processing...';
        videoUrlInput.disabled = true;
        
        // Show progress section, hide result section
        progressSection.style.display = 'block';
        resultSection.style.display = 'none';
        
        // Reset progress
        resetProgress();
        
        // Send request to start processing
        fetch('/api/process', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url: url })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                showNotification(data.error, 'error');
                resetProcessingState();
            } else {
                addLog('info', data.message);
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showNotification('Failed to start processing', 'error');
            resetProcessingState();
        });
    }
    
    /**
     * Update progress display
     */
    function updateProgress(stage, message, percent) {
        // Update progress bar
        progressBar.style.width = percent + '%';
        progressText.textContent = percent + '%';
        
        // Add log entry
        const logType = stage === 'error' ? 'error' : 
                        stage === 'complete' ? 'success' : 'info';
        addLog(logType, message);
        
        // Update step indicators
        updateStepIndicators(stage, percent);
        
        // Handle completion
        if (stage === 'complete' || stage === 'error') {
            resetProcessingState();
            
            if (stage === 'error') {
                showNotification(message, 'error');
            }
        }
    }
    
    /**
     * Update step indicator icons
     */
    function updateStepIndicators(currentStage, percent) {
        const stages = ['info', 'download', 'transcribe', 'visual', 'image', 'evaluate', 'upload'];
        const currentIndex = stages.indexOf(currentStage);
        
        stages.forEach((stage, index) => {
            const stepElement = document.getElementById('step-' + stage);
            if (!stepElement) return;
            
            // Remove all state classes
            stepElement.classList.remove('active', 'completed', 'error');
            
            if (currentStage === 'error') {
                if (index === currentIndex || index < currentIndex) {
                    stepElement.classList.add('error');
                }
            } else if (currentStage === 'complete') {
                stepElement.classList.add('completed');
            } else if (index < currentIndex) {
                stepElement.classList.add('completed');
            } else if (index === currentIndex) {
                stepElement.classList.add('active');
            }
        });
    }
    
    /**
     * Display completed recipe
     */
    function displayRecipe(recipe) {
        if (!recipe) return;
        
        resultSection.style.display = 'block';
        
        let html = `<h3>${escapeHtml(recipe.name || 'Untitled Recipe')}</h3>`;
        
        if (recipe.description) {
            html += `<p>${escapeHtml(recipe.description)}</p>`;
        }
        
        if (recipe.recipeIngredient && recipe.recipeIngredient.length > 0) {
            html += '<h4>Ingredients:</h4><ul>';
            recipe.recipeIngredient.forEach(ing => {
                html += `<li>${escapeHtml(ing)}</li>`;
            });
            html += '</ul>';
        }
        
        if (recipe.recipeInstructions && recipe.recipeInstructions.length > 0) {
            html += '<h4>Instructions:</h4><ol>';
            recipe.recipeInstructions.forEach(inst => {
                const text = typeof inst === 'object' ? inst.text : inst;
                html += `<li>${escapeHtml(text)}</li>`;
            });
            html += '</ol>';
        }
        
        recipePreview.innerHTML = html;
        
        showNotification('Recipe created successfully!', 'success');
    }
    
    /**
     * Add log entry
     */
    function addLog(type, message) {
        const now = new Date();
        const timeStr = now.toLocaleTimeString();
        
        const logEntry = document.createElement('div');
        logEntry.className = 'log-entry log-' + type;
        logEntry.innerHTML = `<span class="log-time">[${timeStr}]</span> ${escapeHtml(message)}`;
        
        logOutput.appendChild(logEntry);
        logOutput.scrollTop = logOutput.scrollHeight;
    }
    
    /**
     * Reset progress indicators
     */
    function resetProgress() {
        progressBar.style.width = '0%';
        progressText.textContent = '0%';
        logOutput.innerHTML = '';
        
        // Reset all step indicators
        document.querySelectorAll('.progress-step').forEach(step => {
            step.classList.remove('active', 'completed', 'error');
        });
    }
    
    /**
     * Reset processing state
     */
    function resetProcessingState() {
        isProcessing = false;
        processBtn.disabled = false;
        processBtn.innerHTML = '<i class="fas fa-magic"></i> Extract Recipe';
        videoUrlInput.disabled = false;
    }
    
    /**
     * Reset entire UI for new recipe
     */
    function resetUI() {
        videoUrlInput.value = '';
        progressSection.style.display = 'none';
        resultSection.style.display = 'none';
        resetProgress();
        resetProcessingState();
        videoUrlInput.focus();
    }
    
    /**
     * Validate URL format
     */
    function isValidUrl(string) {
        try {
            new URL(string);
            return true;
        } catch (_) {
            return false;
        }
    }
    
    /**
     * Show notification
     */
    function showNotification(message, type) {
        // Create flash message element
        const flashContainer = document.querySelector('.flash-messages') || createFlashContainer();
        
        const flashMessage = document.createElement('div');
        flashMessage.className = 'flash-message flash-' + type;
        flashMessage.innerHTML = `
            ${escapeHtml(message)}
            <button class="flash-close" onclick="this.parentElement.remove()">&times;</button>
        `;
        
        flashContainer.appendChild(flashMessage);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            if (flashMessage.parentElement) {
                flashMessage.remove();
            }
        }, 5000);
    }
    
    /**
     * Create flash messages container if it doesn't exist
     */
    function createFlashContainer() {
        const container = document.createElement('div');
        container.className = 'flash-messages';
        document.body.appendChild(container);
        return container;
    }
    
    /**
     * Escape HTML to prevent XSS
     */
    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
});
