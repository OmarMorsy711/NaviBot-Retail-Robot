require('dotenv').config();

const express = require("express");
const path = require("path");

const app = express();
const PORT = 3000;
const BACKEND = "http://127.0.0.1:8000";

// Middleware
app.use(express.json());

// Serve static files from both UI folders
app.use("/auth", express.static(path.join(__dirname, "public_auth")));
app.use("/ui", express.static(path.join(__dirname, "public_ui")));

// Serve public_ui at root for easy access to CSS and JS
app.use(express.static(path.join(__dirname, "public_ui")));

// Debug logger
app.use((req, res, next) => {
  if (req.url.startsWith('/api/customer/screen-state')) {
    return next();
  }
  console.log(`[${new Date().toLocaleTimeString()}] ${req.method} ${req.url}`);
  next();
});

// ---------------------------------------------------------
// ROUTE SHORTCUTS (HTML Pages)
// ---------------------------------------------------------

// Default route -> index.html (UI home)
app.get("/", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "index.html"));
});

// UI Pages shortcuts
app.get("/index", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "index.html"));
});

app.get("/locate-product", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "locate-product.html"));
});

app.get("/scan-product", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "scan-product.html"));
});

app.get("/results", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "results.html"));
});

app.get("/product-details", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "product-details.html"));
});

app.get("/navigation", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "navigation.html"));
});

app.get("/scanned-product", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "scanned-product.html"));
});

app.get("/out_of_service", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "out_of_service.html"));
});

app.get("/shutdown", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "shutdown.html"));
});

app.get("/charging_in_progress", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "charging_in_progress.html"));
});

// Auth Pages shortcuts
app.get("/login", (req, res) => {
  res.sendFile(path.join(__dirname, "public_auth", "login.html"));
});

app.get("/dashboard", (req, res) => {
  res.sendFile(path.join(__dirname, "public_auth", "admin-dashboard.html"));
});

app.get("/cart-navigation", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "cart-navigation.html"));
});

app.get("/employee-dashboard", (req, res) => {
  res.sendFile(path.join(__dirname, "public_auth", "employee-dashboard.html"));
});

app.get("/low_battery", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "low_battery.html"));
});

app.get("/list-results", (req, res) => {
  console.log("Serving list-results.html");
  res.sendFile(path.join(__dirname, "public_ui", "list-results.html"));
});

app.get("/out_of_service", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "out_of_service.html"));
});

app.get("/shutdown", (req, res) => {
  res.sendFile(path.join(__dirname, "public_ui", "shutdown.html"));
});

// ---------------------------------------------------------
// 1. AUTHENTICATION (proxy to FastAPI)
// ---------------------------------------------------------
app.post("/api/login", async (req, res) => {
  const r = await fetch(`${BACKEND}/api/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req.body || {})
  });

  let data = {};
  try {
    data = await r.json();
  } catch {
    data = { detail: "backend did not return json" };
  }

  res.status(r.status).json(data);
});

// ---------------------------------------------------------
// 2. PRODUCT SEARCH PROXY (for locate-product.html)
// ---------------------------------------------------------
app.get("/api/search", async (req, res) => {
  try {
    const query = req.query.q;
    const response = await fetch(`${BACKEND}/api/products/search?q=${encodeURIComponent(query)}`);
    const data = await response.json();
    // Transform response to match frontend expectations
    res.status(response.status).json({ results: data.products || [] });
  } catch (err) {
    console.error("Search Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

// ---------------------------------------------------------
// 3. PRODUCT DETAILS PROXY (for product-details.html)
// ---------------------------------------------------------
app.get("/api/products/:sku", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/products/${req.params.sku}`);
    const data = await response.json();
    // Return the product directly (frontend expects { name, price, sku, ... })
    res.status(response.status).json(data.product || data);
  } catch (err) {
    console.error("Product Detail Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

// ---------------------------------------------------------
// 4. NAVIGATION PROXY (to move the robot)
// ---------------------------------------------------------
app.post("/api/navigate", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/navigate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body)
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Navigation Error:", err.message);
    res.status(500).json({ error: "Robot controller unreachable" });
  }
});

// ---------------------------------------------------------
// 5. RETURN TO BASE PROXY (to go home)
// ---------------------------------------------------------
app.post("/api/return_to_base", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/return_to_base`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Return to Base Error:", err.message);
    res.status(500).json({ error: "Failed to send robot home" });
  }
});

app.post("/api/user/override/cancel_navigation", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/user/override/cancel_navigation`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("User Cancel Override Error:", err.message);
    res.status(500).json({ error: "Failed to execute user cancel override" });
  }
});

app.post("/api/admin/override/stop", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/admin/override/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Admin Override Stop Error:", err.message);
    res.status(500).json({ error: "Failed to execute admin override stop" });
  }
});

app.post("/api/admin/override/power_off", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/admin/override/power_off`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Admin Override Power Off Error:", err.message);
    res.status(500).json({ error: "Failed to execute admin override power off" });
  }
});

app.post("/api/admin/override/return_to_base", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/admin/override/return_to_base`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Admin Override Return To Base Error:", err.message);
    res.status(500).json({ error: "Failed to execute admin override return to base" });
  }
});

app.post("/api/admin/override/reset_references", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/admin/override/reset_references`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Admin Override Reset References Error:", err.message);
    res.status(500).json({ error: "Failed to execute admin override reset references" });
  }
});

app.post("/api/admin/override/patrol", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/admin/override/patrol`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Admin Override Patrol Error:", err.message);
    res.status(500).json({ error: "Failed to execute admin override patrol" });
  }
});

app.get("/api/admin/customer-in-use", async (req, res) => {
  try {
    const windowSeconds = req.query.window_seconds ? Number(req.query.window_seconds) : 45;
    const response = await fetch(`${BACKEND}/api/admin/customer-in-use?window_seconds=${encodeURIComponent(windowSeconds)}`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Admin Customer In Use Check Error:", err.message);
    res.status(500).json({ error: "Failed to check customer in-use status" });
  }
});

// ---------------------------------------------------------
// 5.2 MANUAL STOP PROXY
// ---------------------------------------------------------
app.post("/api/stop", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Stop Error:", err.message);
    res.status(500).json({ error: "Failed to stop robot" });
  }
});

app.post("/api/power_on", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/power_on`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Power On Error:", err.message);
    res.status(500).json({ error: "Failed to power on robot" });
  }
});

// ---------------------------------------------------------
// 6. CONFIG PROXY (for dashboard Supabase config)
// ---------------------------------------------------------
app.get("/api/config", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/config`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Config Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

// ---------------------------------------------------------
// 7. LOGS PROXY (for dashboard command logs)
// ---------------------------------------------------------
app.get("/api/logs", async (req, res) => {
  try {
    const limit = req.query.limit || 50;
    const commandType = req.query.command_type;
    const params = new URLSearchParams({ limit: String(limit) });
    if (commandType) params.set("command_type", String(commandType));

    const response = await fetch(`${BACKEND}/api/logs?${params.toString()}`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Logs Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

app.get("/api/logs/command-types", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/logs/command-types`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Log Command Types Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

app.get("/api/navigation/status", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/navigation/status`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Navigation Status Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

app.get("/api/alerts", async (req, res) => {
  try {
    const limit = req.query.limit || 100;
    const response = await fetch(`${BACKEND}/api/alerts?limit=${encodeURIComponent(String(limit))}`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Alerts Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

app.post("/api/alerts/:alertId/dismiss", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/alerts/${encodeURIComponent(req.params.alertId)}/dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {})
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Alert Dismiss Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

app.get("/api/customer/screen-state", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/customer/screen-state`);
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Customer Screen State Error:", err.message);
    res.status(500).json({ error: "Backend unreachable" });
  }
});

// ---------------------------------------------------------
// 8. DEPLOY ZONE PROXY (for dashboard robot deployment)
// ---------------------------------------------------------
app.post("/api/deploy_zone", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/deploy_zone`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body)
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Deploy Zone Error:", err.message);
    res.status(500).json({ error: "Robot controller unreachable" });
  }
});

// ---------------------------------------------------------
// 9. ANALYZE VISION PROXY (for scan-product OpenCV)
// ---------------------------------------------------------
app.post("/api/analyze-vision", async (req, res) => {
  try {
    const response = await fetch(`${BACKEND}/api/analyze-vision`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body)
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Vision Analysis Error:", err.message);
    // Return a demo/fallback response if backend doesn't have this endpoint yet
    res.status(500).json({ error: "Vision analysis unavailable" });
  }
});

// ---------------------------------------------------------
// 10. GROQ NLP PROXY (UPDATED - FIXED VERSION)
// ---------------------------------------------------------
const multer = require('multer');
const upload = multer({ storage: multer.memoryStorage() });

app.post("/api/transcribe", upload.single('audio'), async (req, res) => {
  console.log("=== GROQ NLP REQUEST RECEIVED ===");
  
  try {
    const GROQ_API_KEY = process.env.GROQ_API_KEY;
    
    const language = req.body.language || 'en';
    console.log("Language from request:", language);
    console.log("File received:", req.file ? `Size: ${req.file.size} bytes, Type: ${req.file.mimetype}` : 'No file');

    // Check if file was received
    if (!req.file) {
      return res.status(400).json({ error: "No audio file uploaded" });
    }

    // Step 1: Transcribe the audio to text
    const formData = new FormData();
    
    // Create a blob from the buffer
    const audioBlob = new Blob([req.file.buffer], { type: req.file.mimetype });
    formData.append('file', audioBlob, {
      filename: 'audio.webm',
      contentType: req.file.mimetype
    });
    
    formData.append('model', 'whisper-large-v3');
    formData.append('response_format', 'json');
    
    let apiUrl = 'https://api.groq.com/openai/v1/audio/transcriptions';
    
    if (language !== 'en') {
      apiUrl = 'https://api.groq.com/openai/v1/audio/translations';
      console.log("Using translations endpoint for non-English audio");
    } else {
      formData.append('language', 'en');
    }

    // Get form data as buffer - Fix for Node.js environment
    const formDataBuffer = await new Promise((resolve, reject) => {
      const chunks = [];
      formData.getReader ? 
        // Browser-like FormData (unlikely in Node.js)
        reject(new Error("Unexpected FormData type")) :
        // For node-fetch or similar
        resolve(Buffer.from(JSON.stringify({
          error: "FormData not directly supported in Node.js without proper polyfill"
        })));
    });

    // Instead, let's use a different approach for Node.js
    const fetch = require('node-fetch');
    
    // Create a proper multipart form using form-data package
    const FormDataNode = require('form-data');
    const nodeFormData = new FormDataNode();
    
    nodeFormData.append('file', req.file.buffer, {
      filename: 'audio.webm',
      contentType: req.file.mimetype
    });
    nodeFormData.append('model', 'whisper-large-v3');
    nodeFormData.append('response_format', 'json');
    if (language === 'en') {
      nodeFormData.append('language', 'en');
    }
    
    // Get headers with boundary
    const headers = nodeFormData.getHeaders();

    const transcriptionResponse = await fetch(apiUrl, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${GROQ_API_KEY}`,
        ...headers
      },
      body: nodeFormData
    });

    const transcriptionData = await transcriptionResponse.json();
    
    if (!transcriptionResponse.ok) {
      throw new Error(transcriptionData.error?.message || 'Transcription failed');
    }

    const transcribedText = transcriptionData.text.trim();
    console.log(`Transcribed: "${transcribedText}"`);

    // Step 2: Use Groq's LLM to extract the product name
    const llmResponse = await fetch('https://api.groq.com/openai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${GROQ_API_KEY}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        model: 'llama-3.3-70b-versatile',
        messages: [
          {
            role: 'system',
            content: `You are a product extractor for a grocery store. 
            Extract ONLY the main product name from the user's question.
            Return ONLY the product name in lowercase and in ENGLISH, nothing else.
            
            Examples:
            User: "Can you show me where is milk" → milk
            User: "Is there milk in this store" → milk
            User: "I'm looking for apple juice" → apple juice
            User: "Do you have bread" → bread
            User: "Where can I find cereal" → cereal
            User: "Ou sont les cereales" → cereal
            User: "I need some oranges" → oranges
            User: "Do you sell cheese" → cheese
            User: "Dónde está la leche" → milk
            User: "Wo ist die Milch" → milk
            
            If the user mentions multiple products, return the first one only.
            If no product is mentioned, return "product not found".`
          },
          {
            role: 'user',
            content: transcribedText
          }
        ],
        temperature: 0.1,
        max_tokens: 50
      })
    });

    const llmData = await llmResponse.json();
    
    if (!llmResponse.ok) {
      throw new Error(llmData.error?.message || 'NLP extraction failed');
    }

    let extractedProduct = llmData.choices[0].message.content.trim().toLowerCase();
    console.log(`NLP extracted: "${extractedProduct}"`);

    // Step 3: Clean up the extracted product name
    extractedProduct = extractedProduct.replace(/["'.]/g, '').trim();
    
    // Simple plural fix
    if (extractedProduct.endsWith('s') && 
        !extractedProduct.endsWith('ss') &&
        !extractedProduct.endsWith('us') &&
        extractedProduct.length > 3) {
      extractedProduct = extractedProduct.slice(0, -1);
      console.log(`Removed trailing 's': "${extractedProduct}"`);
    }

    // Step 4: Final check
    if (extractedProduct === 'product not found' || extractedProduct === 'none' || extractedProduct === '') {
      extractedProduct = '';
      console.log("No product found in the user's query");
    }

    console.log(`✅ Final search query: "${extractedProduct}"`);
    res.json({ text: extractedProduct });
    
  } catch (err) {
    console.error("❌ Groq NLP Error:", err.message);
    res.status(500).json({ error: "Transcription failed: " + err.message });
  }
});

// ---------------------------------------------------------
// VISION SYSTEM PROXY (connects to app_camera.py)
// ---------------------------------------------------------

// Start product scan
app.post("/api/vision/start-product-scan", async (req, res) => {
  try {
    const response = await fetch('http://127.0.0.1:5001/start_product_scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Start scan error:", err.message);
    res.status(500).json({ error: "Vision system unreachable" });
  }
});

// Proxy for video feed
app.get("/api/vision/video-feed", async (req, res) => {
  try {
    // This is a special case - we need to pipe the stream
    const response = await fetch('http://127.0.0.1:5001/video_feed');
    
    // Set the same content type
    res.setHeader('Content-Type', response.headers.get('content-type'));
    
    // Pipe the response body
    response.body.pipe(res);
  } catch (err) {
    console.error("Video feed error:", err.message);
    res.status(500).send("Video feed unavailable");
  }
});

// Check detection status
app.get("/api/vision/detection-status", async (req, res) => {
  try {
    const response = await fetch('http://127.0.0.1:5001/detection_status');
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Detection status error:", err.message);
    res.status(500).json({ error: "Vision system unreachable" });
  }
});

// Reset detection
app.post("/api/vision/reset-detection", async (req, res) => {
  try {
    const response = await fetch('http://127.0.0.1:5001/reset_detection', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Reset detection error:", err.message);
    res.status(500).json({ error: "Vision system unreachable" });
  }
});

// Cancel scan
app.post("/api/vision/cancel-scan", async (req, res) => {
  try {
    const response = await fetch('http://127.0.0.1:5001/cancel_scan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Cancel scan error:", err.message);
    res.status(500).json({ error: "Vision system unreachable" });
  }
});

// Health check
app.get("/api/vision/health", async (req, res) => {
  try {
    const response = await fetch('http://127.0.0.1:5001/health');
    const data = await response.json();
    res.status(response.status).json(data);
  } catch (err) {
    console.error("Health check error:", err.message);
    res.status(500).json({ error: "Vision system unreachable" });
  }
});

// ---------------------------------------------------------
// START SERVER
// ---------------------------------------------------------
app.listen(PORT, () => {
  console.log(`
  Server Running
  ----------------------------------
  Local: http://localhost:${PORT}
  Backend API: ${BACKEND}
  ----------------------------------
  Available Routes:
  - /           -> Home (index.html)
  - /index      -> Home
  - /login      -> Admin Login
  - /dashboard  -> Admin Dashboard
  - /locate-product
  - /scan-product
  - /results
  - /product-details
  - /navigation
  - /scanned-product
  `);
});
