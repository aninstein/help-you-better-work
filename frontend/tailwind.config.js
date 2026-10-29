/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', 'PingFang SC', 'sans-serif'],
      },
      colors: {
        ink: '#090b12',
        panel: '#11141f',
        talker: '#38d9c5',
        hinter: '#f5ad4e',
        ai: '#9b8cff',
      },
      boxShadow: {
        glow: '0 0 40px rgba(155, 140, 255, 0.14)',
        cyan: '0 0 32px rgba(56, 217, 197, 0.18)',
      },
      keyframes: {
        pulseSoft: { '0%, 100%': { opacity: '0.45' }, '50%': { opacity: '1' } },
        rise: { '0%': { transform: 'translateY(8px)', opacity: '0' }, '100%': { transform: 'translateY(0)', opacity: '1' } },
        wave: { '0%, 100%': { transform: 'scaleY(.35)' }, '50%': { transform: 'scaleY(1)' } },
      },
      animation: {
        'pulse-soft': 'pulseSoft 2s ease-in-out infinite',
        rise: 'rise .45s ease-out both',
        wave: 'wave .9s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
