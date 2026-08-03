/**
 * Hybrid Data Service for NSP Cosmetic POS
 * Handles switching between Local Flask API and Cloud Firestore SDK.
 */

const DatabaseProvider = {
    LOCAL: 'LOCAL',
    CLOUD: 'CLOUD'
};

/**
 * Detects the database provider based on hostname or URL parameters.
 */
function getDatabaseProvider() {
    const params = new URLSearchParams(window.location.search);
    const modeParam = params.get('mode')?.toUpperCase();

    // 1. Explicit URL parameter toggle (?mode=local or ?mode=cloud)
    if (modeParam && DatabaseProvider[modeParam]) {
        return DatabaseProvider[modeParam];
    }

    // 2. Detection by Hostname (Main PC vs Remote)
    const localHosts = ['localhost', '127.0.0.1', '0.0.0.0'];
    if (localHosts.includes(window.location.hostname)) {
        return DatabaseProvider.LOCAL;
    }

    // Default to CLOUD for remote browsers (tablets/remote access)
    return DatabaseProvider.CLOUD;
}

class DataService {
    constructor() {
        this.provider = getDatabaseProvider();
        console.log(`[DataService] Initialized with ${this.provider} mode.`);
        
        if (this.provider === DatabaseProvider.CLOUD) {
            this.initFirebase();
        }
    }

    /**
     * Initialize Firebase Web SDK for Cloud Mode
     * Note: Ensure Firebase SDK scripts are loaded in your HTML.
     */
    initFirebase() {
        // These would typically come from your .env or Firebase Console
        const firebaseConfig = {
            // apiKey: "YOUR_API_KEY",
            // authDomain: "your-app.firebaseapp.com",
            // projectId: "your-app-id",
            // storageBucket: "your-app.appspot.com",
            // messagingSenderId: "...",
            // appId: "..."
        };

        if (typeof firebase !== 'undefined') {
            if (!firebase.apps.length) {
                firebase.initializeApp(firebaseConfig);
            }
            this.db = firebase.firestore();
        } else {
            console.warn("Firebase SDK not detected. Direct Firestore calls will fail.");
        }
    }

    /**
     * Get Products from the appropriate source
     */
    async getProducts() {
        if (this.provider === DatabaseProvider.LOCAL) {
            console.log("Fetching products from Local Flask API...");
            const response = await fetch('/api/products'); // Assuming you have this endpoint
            return await response.json();
        } else {
            console.log("Fetching products from Cloud Firestore...");
            return await this.getProductsFromFirestore();
        }
    }

    /**
     * Read-Only sync from Firestore
     */
    async getProductsFromFirestore() {
        if (!this.db) return [];
        try {
            const snapshot = await this.db.collection('items').get();
            return snapshot.docs.map(doc => ({ id: doc.id, ...doc.to_dict() }));
        } catch (error) {
            console.error("Firestore Error:", error);
            return [];
        }
    }

    /**
     * Example method to wrap any fetch call
     */
    async request(endpoint, options = {}) {
        if (this.provider === DatabaseProvider.LOCAL) {
            return await fetch(endpoint, options);
        } else {
            // For Cloud mode, you might want to redirect some calls to a Cloud Function
            // or handle them via specific Firestore logic.
            console.warn("Request redirected or handled by Cloud Logic.");
            return null; 
        }
    }
}

// Global instance
window.dataService = new DataService();
