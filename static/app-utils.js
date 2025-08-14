/**
 * Utility functions for the Seating Planner app
 */

// Helper function to refresh page and clear cache
function hardRefresh() {
    // Clear cache and reload
    window.location.reload(true);
}

// Function to check image loading
function checkImage(imgElement) {
    if (imgElement.complete && imgElement.naturalHeight !== 0) {
        console.log('Image loaded successfully');
        return true;
    } else {
        console.log('Image failed to load');
        return false;
    }
}

// Force image reload by adding timestamp
function reloadImage(imgElement) {
    const originalSrc = imgElement.src.split('?')[0];
    imgElement.src = originalSrc + '?t=' + new Date().getTime();
    console.log('Reloaded image:', imgElement.src);
}

// Logging wrapper
function logDebug(message) {
    console.log(`[DEBUG] ${message}`);
}

// Export to global scope
window.appUtils = {
    hardRefresh,
    checkImage,
    reloadImage,
    logDebug
};

// Auto-initialize
document.addEventListener('DOMContentLoaded', function() {
    console.log('App utils loaded');
    
    // Check logo image
    const logo = document.querySelector('.logo');
    if (logo) {
        if (!checkImage(logo)) {
            console.log('Logo not loaded correctly, attempting reload');
            setTimeout(() => reloadImage(logo), 500);
        }
    }
});
