// ========== GLOBAL TRANSLATIONS ==========
const translations = {
    en: {
        tagline: "Your personal automated shopping assistant",
        locateTitle: "Locate",
        locateDesc: "Find items by name and I'll take you there",
        scanTitle: "Scan",
        scanDesc: "Show me an item to check price or location"
    },
    ar: {
        tagline: "مساعد التسوق الآلي الشخصي الخاص بك",
        locateTitle: "تحديد الموقع",
        locateDesc: "ابحث عن العناصر بالاسم وسأخذك إليها",
        scanTitle: "مسح",
        scanDesc: "أرني عنصرًا للتحقق من السعر أو الموقع"
    },
    fr: {
        tagline: "Votre assistant de shopping automatisé personnel",
        locateTitle: "Localiser",
        locateDesc: "Trouvez des articles par nom et je vous y emmène",
        scanTitle: "Scanner",
        scanDesc: "Montrez-moi un article pour vérifier le prix ou l'emplacement"
    },
    hi: {
        tagline: "आपका व्यक्तिगत स्वचालित शॉपिंग सहायक",
        locateTitle: "ढूंढें",
        locateDesc: "नाम से आइटम खोजें और मैं आपको वहां ले जाऊंगा",
        scanTitle: "स्कैन करें",
        scanDesc: "मूल्य या स्थान जांचने के लिए मुझे एक आइटम दिखाएं"
    },
    ur: {
        tagline: "آپ کا ذاتی خودکار شاپنگ اسسٹنٹ",
        locateTitle: "مقام تلاش کریں",
        locateDesc: "نام سے اشیاء تلاش کریں اور میں آپ کو وہاں لے جاؤں گا",
        scanTitle: "اسکین کریں",
        scanDesc: "قیمت یا مقام چیک کرنے کے لیے مجھے ایک آئٹم دکھائیں"
    },
    zh: {
        tagline: "您的个人自动购物助手",
        locateTitle: "定位",
        locateDesc: "按名称查找物品，我会带您去那里",
        scanTitle: "扫描",
        scanDesc: "向我展示物品以检查价格或位置"
    }
};

// ========== LANGUAGE FUNCTIONS ==========
function toggleLanguageDropdown() {
    const dropdown = document.getElementById('languageDropdown');
    if (dropdown) dropdown.classList.toggle('show');
}

// Close dropdown when clicking outside
document.addEventListener('click', function(event) {
    const container = document.querySelector('.language-container');
    const dropdown = document.getElementById('languageDropdown');
    
    if (container && dropdown && !container.contains(event.target)) {
        dropdown.classList.remove('show');
    }
});

function changeLanguage(langCode, langName, event) {
    // Update selected language text
    const selectedLang = document.getElementById('selectedLanguage');
    if (selectedLang) selectedLang.textContent = langName;
    
    // Update active class in dropdown
    const options = document.querySelectorAll('.language-option');
    options.forEach(option => {
        option.classList.remove('active');
    });
    if (event) {
        event.target.closest('.language-option').classList.add('active');
    }
    
    // Update all text content based on selected language
    if (translations[langCode]) {
        const elements = {
            tagline: document.getElementById('tagline'),
            locateTitle: document.getElementById('locateTitle'),
            locateDesc: document.getElementById('locateDesc'),
            scanTitle: document.getElementById('scanTitle'),
            scanDesc: document.getElementById('scanDesc')
        };
        
        for (let [id, element] of Object.entries(elements)) {
            if (element && translations[langCode][id]) {
                element.textContent = translations[langCode][id];
            }
        }
    }
    
    // Close dropdown
    const dropdown = document.getElementById('languageDropdown');
    if (dropdown) dropdown.classList.remove('show');
    
    // Save language preference to localStorage
    localStorage.setItem('preferredLanguage', langCode);
}

// ========== BATTERY FUNCTIONS (Shared Across Pages) ==========
let batteryLevel;

// Initialize or load battery level from localStorage
function initBattery() {
    const savedBattery = localStorage.getItem('globalBatteryLevel');
    const lastUpdate = localStorage.getItem('batteryLastUpdate');
    
    if (savedBattery && lastUpdate) {
        // Calculate how much battery should have drained since last visit
        const timeDiff = (Date.now() - parseInt(lastUpdate)) / 1000; // in seconds
        const drainAmount = timeDiff * 0.1; // 0.1% per second
        batteryLevel = Math.max(0, parseFloat(savedBattery) - drainAmount);
    } else {
        batteryLevel = 100; // Start at 100% if no saved data
    }
    
    // If battery reached 0 or below, reset to 100
    if (batteryLevel <= 0) {
        batteryLevel = 100;
    }
    
    updateBatteryDisplay();
}

function updateBatteryDisplay() {
    // Add safety check
    if (typeof batteryLevel !== 'number' || isNaN(batteryLevel)) {
        batteryLevel = 100;
    }
    
    const batteryLevelElem = document.getElementById('batteryLevel');
    const batteryText = document.getElementById('batteryText');
    const batteryIcon = document.getElementById('batteryIcon');
    
    if (batteryLevelElem) {
        batteryLevelElem.style.width = Math.floor(batteryLevel) + '%';
    }
    if (batteryText) {
        batteryText.textContent = Math.floor(batteryLevel) + '%';
    }
    
    // Update color based on level
    if (batteryIcon) {
        batteryIcon.classList.remove('low', 'medium');
        if (batteryLevel < 20) {
            batteryIcon.classList.add('low');
        } else if (batteryLevel < 50) {
            batteryIcon.classList.add('medium');
        }
    }
}

function updateBattery() {
    batteryLevel = Math.max(0, batteryLevel - 0.1);
    
    // Save to localStorage with timestamp
    localStorage.setItem('globalBatteryLevel', batteryLevel.toString());
    localStorage.setItem('batteryLastUpdate', Date.now().toString());
    
    updateBatteryDisplay();
    
    // Check if battery reached 20% or below
    if (batteryLevel <= 20) {
        // Reset to 100%
        batteryLevel = 100;
        localStorage.setItem('globalBatteryLevel', '100');
        localStorage.setItem('batteryLastUpdate', Date.now().toString());
        updateBatteryDisplay();
        
        // Continue the cycle
        setTimeout(updateBattery, 1000);
    } else {
        // Continue normal drain
        setTimeout(updateBattery, 1000);
    }
}

// ========== PAGE LOAD INITIALIZATION ==========
document.addEventListener('DOMContentLoaded', function() {
    // Initialize battery
    initBattery();
    
    // Start battery drain cycle
    setTimeout(() => {
        if (typeof batteryLevel === 'number' && !isNaN(batteryLevel)) {
            updateBattery();
        }
    }, 1000);

    // Load saved language preference
    const savedLang = localStorage.getItem('preferredLanguage');
    if (savedLang && translations[savedLang]) {
        // Find the language name
        let langName = '';
        switch(savedLang) {
            case 'en': langName = 'English'; break;
            case 'ar': langName = 'العربية'; break;
            case 'fr': langName = 'Français'; break;
            case 'hi': langName = 'हिन्दी'; break;
            case 'ur': langName = 'اردو'; break;
            case 'zh': langName = '中文'; break;
        }
        
        // Update UI
        const selectedLang = document.getElementById('selectedLanguage');
        if (selectedLang) selectedLang.textContent = langName;
        
        // Update text content
        if (translations[savedLang]) {
            const elements = {
                tagline: document.getElementById('tagline'),
                locateTitle: document.getElementById('locateTitle'),
                locateDesc: document.getElementById('locateDesc'),
                scanTitle: document.getElementById('scanTitle'),
                scanDesc: document.getElementById('scanDesc')
            };
            
            for (let [id, element] of Object.entries(elements)) {
                if (element && translations[savedLang][id]) {
                    element.textContent = translations[savedLang][id];
                }
            }
        }
        
        // Update active class in dropdown
        const options = document.querySelectorAll('.language-option');
        options.forEach((option, index) => {
            option.classList.remove('active');
            if ((savedLang === 'en' && index === 0) ||
                (savedLang === 'ar' && index === 1) ||
                (savedLang === 'fr' && index === 2) ||
                (savedLang === 'hi' && index === 3) ||
                (savedLang === 'ur' && index === 4) ||
                (savedLang === 'zh' && index === 5)) {
                option.classList.add('active');
            }
        });
    }
});

// ========== SCREEN STATE SYNC (Only for index page) ==========
let screenStateInterval;
let screenStateSyncInFlight = false;

async function syncCustomerScreenState() {
    if (screenStateSyncInFlight) return;
    screenStateSyncInFlight = true;
    try {
        const response = await fetch('/api/customer/screen-state');
        const data = await response.json();

        if (!data.ok) return;

        // Only redirect on index page
        const isIndexPage = window.location.pathname === '/' || 
                           window.location.pathname === '/index' ||
                           window.location.pathname === '';

        if (!isIndexPage) return;

        if (data.mode === 'black') {
            window.location.href = '/shutdown';
            return;
        }

        if (data.mode === 'out_of_service') {
            window.location.href = '/out_of_service';
            return;
        }

        // If mode is 'charging' or 'normal', stay on index

    } catch (error) {
        console.error('Screen state sync error:', error);
    } finally {
        screenStateSyncInFlight = false;
    }
}

// Start screen state polling only on index page
const isIndexPage = window.location.pathname === '/' || 
                   window.location.pathname === '/index' ||
                   window.location.pathname === '';

if (isIndexPage) {
    // Initial check
    syncCustomerScreenState();
    // Poll every 2 seconds
    screenStateInterval = setInterval(syncCustomerScreenState, 2000);
}

// Clean up on page unload
window.addEventListener('beforeunload', function() {
    if (screenStateInterval) clearInterval(screenStateInterval);
});