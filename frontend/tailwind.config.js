/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'mnc-brand': '#020617',
        'mnc-accent': '#3b82f6',
        'mnc-glass': 'rgba(15, 23, 42, 0.8)',
        'safety-green': '#10b981',
        'danger-red': '#ef4444',
      },
    },
  },
  plugins: [],
}
