// ========== CLEAR SCAN DATA ==========
function clearScanData() {
    // Reset detection on vision server
    fetch('http://localhost:5001/reset_detection', {
        method: 'POST'
    }).catch(err => console.error('Reset error:', err));
    
    // Clear localStorage scan data
    localStorage.removeItem('scannedSku');
    localStorage.removeItem('scannedCategory');
    localStorage.removeItem('scanMode');
    
    console.log('Scan data cleared');
}

// ========== SCAN AGAIN ==========
function scanAgain() {
    clearScanData();
    window.location.href = '/scan-product';
}

// ========== LOAD SCANNED PRODUCT(S) ==========
async function loadScannedProduct() {
    const scanMode = localStorage.getItem('scanMode');
    const sku = localStorage.getItem('scannedSku');
    const category = localStorage.getItem('scannedCategory');
    
    const statusText = document.getElementById('statusText');
    const priceCard = document.getElementById('priceCard');
    const scrollContainer = document.getElementById('scrollContainer');
    const pageTitle = document.getElementById('pageTitle');
    
    if (!scanMode || (!sku && !category)) {
        statusText.textContent = "No product scanned. Please try again.";
        return;
    }

    try {
        let products = [];
        
        if (scanMode === 'single' && sku) {
            // Fetch single product
            pageTitle.textContent = "Product Price";
            const response = await fetch(`/api/products/${sku}`);
            
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const data = await response.json();
            const product = data.product || data;
            
            // Hide grid, show single card
            scrollContainer.style.display = 'none';
            priceCard.style.display = 'block';
            
            // Populate single product details
            document.getElementById('itemName').textContent = product.name;
            document.getElementById('itemPrice').textContent = product.price.toFixed(2);
            document.getElementById('itemImg').src = product.image_url || 'https://via.placeholder.com/200?text=' + encodeURIComponent(product.name);
            
        } else if (scanMode === 'category' && category) {
            // Fetch all products in category
            pageTitle.textContent = `${category} Products`;
            
            const response = await fetch(`/api/search?q=${encodeURIComponent(category)}`);
            const data = await response.json();
            
            if (data.results && data.results.length > 0) {
                products = data.results;
                
                // Hide single card, show grid
                priceCard.style.display = 'none';
                
                // Display products in grid
                const grid = document.getElementById('productsGrid');
                scrollContainer.style.display = 'block';
                
                // Clear any existing products
                grid.innerHTML = '';
                
                products.forEach(product => {
                    const card = document.createElement('div');
                    card.className = 'product-card';
                    
                    card.innerHTML = `
                        <img src="${product.image_url || 'https://via.placeholder.com/200?text=' + encodeURIComponent(product.name)}" class="product-image" alt="${product.name}">
                        <div>
                            <div class="product-name">${product.name}</div>
                            <div class="price-label">Price</div>
                            <div class="product-price">AED ${product.price}</div>
                        </div>
                    `;
                    
                    grid.appendChild(card);
                });
            } else {
                throw new Error("No products found in category");
            }
        }

        // Hide status text once loaded
        statusText.style.display = 'none';
        
    } catch (error) {
        console.error("Error loading product:", error);
        statusText.innerHTML = "Error loading product.<br>Please try again.";
    }
}

// ========== PAGE LOAD ==========
document.addEventListener('DOMContentLoaded', function() {
    loadScannedProduct();
});

// Clean up when leaving page
window.addEventListener('beforeunload', function() {
    // Optional: Any cleanup needed
});