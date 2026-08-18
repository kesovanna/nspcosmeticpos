// Initialize Firebase only if online
let app = null;
let db = null;

if (navigator.onLine) {
    try {
        // Dynamic import to prevent blocking if offline
        import("https://www.gstatic.com/firebasejs/10.8.1/firebase-app.js").then(module => {
            const initializeApp = module.initializeApp;
            const firebaseConfig = {
                projectId: "nsp-cosmetic-store-pos",
                // Add your actual apiKey and other config details here
            };
            app = initializeApp(firebaseConfig);
            
            import("https://www.gstatic.com/firebasejs/10.8.1/firebase-firestore.js").then(firestoreModule => {
                db = firestoreModule.getFirestore(app);
                // Export other firestore functions if needed, but since this is async, 
                // it's better to handle Firebase usage carefully in the app.
            });
        }).catch(e => console.warn("Firebase load failed:", e));
    } catch (e) {
        console.warn("Firebase init failed:", e);
    }
}

export { 
    db
};
