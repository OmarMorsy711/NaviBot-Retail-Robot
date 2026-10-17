// ========== PRODUCT MAPPING ==========
const productMapping = {
    "Water 500ml": { category: "Water" },
    "water": { category: "Water" },
    "Water": { category: "Water" },
    "Pepsi": { sku: "1046" },
    "Coca Cola": { sku: "1053" },
    "CocaCola": { sku: "1053" },
    "Cheerios": { sku: "1002" },
    "Fruit Loops": { sku: "1006" },
    "FruitLoops": { sku: "1006" },
    "Froot Loops": { sku: "1006" },
    "FrootLoops": { sku: "1006" },
    "Lucky Charms": { sku: "1005" },
    "LuckyCharms": { sku: "1005" }
};

// ========== PRODUCT SCANNING (automatic version) ==========
let pollInterval;
let detectionLocked = false;
let cameraActive = false;

async function startProductScan() {
    try {
        document.getElementById('scanningMessage').textContent = 'Starting camera...';
        
        // Tell server to start product scanning mode
        const response = await fetch('http://localhost:5001/start_product_scan', {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error('Failed to start camera');
        }
        
        cameraActive = true;
        console.log('✅ Product scanning started');
        
        // Set the video feed source
        document.getElementById('cameraFeed').src = 'http://localhost:5001/video_feed';
        document.getElementById('scanningMessage').textContent = 'Scanning for products...';
        
        // Start polling for detection status
        startPolling();
        
    } catch (error) {
        console.error('❌ Error starting camera:', error);
        document.getElementById('scanningMessage').textContent = 'Camera error: ' + error.message;
    }
}

async function stopProductScan() {
    if (cameraActive) {
        try {
            await fetch('http://localhost:5001/cancel_scan', { method: 'POST' });
            cameraActive = false;
            document.getElementById('cameraFeed').src = '';
            console.log('🛑 Product scanning stopped');
        } catch (error) {
            console.error('Error stopping camera:', error);
        }
    }
}

function startPolling() {
    pollInterval = setInterval(checkDetection, 500);
}

async function checkDetection() {
    if (!cameraActive) return;
    
    try {
        const response = await fetch('http://localhost:5001/detection_status');
        const data = await response.json();
        
        if (data.locked && data.result && !detectionLocked) {
            detectionLocked = true;
            
            const detectedName = data.result.product_name;
            const productInfo = productMapping[detectedName];
            
            if (productInfo) {
                document.getElementById('loading').style.display = 'flex';
                document.getElementById('loadingText').textContent = `PRODUCT FOUND! REDIRECTING...`;
                
                if (productInfo.category) {
                    localStorage.setItem('scannedCategory', productInfo.category);
                    localStorage.setItem('scanMode', 'category');
                } else if (productInfo.sku) {
                    localStorage.setItem('scannedSku', productInfo.sku);
                    localStorage.setItem('scanMode', 'single');
                }
                
                setTimeout(async () => {
                    await stopProductScan();
                    window.location.href = '/scanned-product';
                }, 1500);
            } else {
                console.log(`Unknown product detected: ${detectedName}`);
                document.getElementById('scanningMessage').textContent = 
                    `Detected: ${detectedName} (not in database)`;
                
                setTimeout(async () => {
                    detectionLocked = false;
                    await fetch('http://localhost:5001/reset_detection', { method: 'POST' });
                }, 3000);
            }
        }
    } catch (error) {
        console.error('Error checking detection:', error);
    }
}

async function resetDetection() {
    try {
        await fetch('http://localhost:5001/reset_detection', { method: 'POST' });
        console.log('🔄 Detection reset');
    } catch (error) {
        console.error('Reset error:', error);
    }
}

// Handle page unload - stop camera
window.addEventListener('beforeunload', async function() {
    await stopProductScan();
    if (pollInterval) clearInterval(pollInterval);
});

window.addEventListener('pagehide', async function() {
    await stopProductScan();
    if (pollInterval) clearInterval(pollInterval);
});

// ========== PAGE LOAD ==========
document.addEventListener('DOMContentLoaded', function() {
    // Start product scan automatically when page loads
    startProductScan();
    
    // Update scanning message periodically
    setInterval(() => {
        if (!detectionLocked && cameraActive) {
            const messages = [
                "Scanning for products...",
                "Hold product steady...",
                "Align in frame..."
            ];
            const randomMsg = messages[Math.floor(Math.random() * messages.length)];
            const msgElement = document.getElementById('scanningMessage');
            if (msgElement) msgElement.textContent = randomMsg;
        }
    }, 3000);
});