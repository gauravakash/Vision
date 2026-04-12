/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        orange: {
          DEFAULT: '#FF5C1A',
          light: 'rgba(255,92,26,0.1)',
          border: 'rgba(255,92,26,0.25)',
        },
        bg: '#FDFAF6',
        card: '#FFFFFF',
        sidebar: '#FAF7F2',
        cream: '#F2EDE4',
        text: {
          primary: '#1A1208',
          secondary: '#5C4D42',
          muted: '#A08880',
        },
        border: 'rgba(0,0,0,0.07)',
        success: '#1A7A4A',
        error: '#C0392B',
        warning: '#C67B00',
      },
      fontFamily: {
        display: ['"Clash Display"', 'sans-serif'],
        sans: ['Satoshi', 'sans-serif'],
        mono: ['"DM Mono"', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        shimmer: 'shimmer 1.5s infinite',
        'spike-pulse': 'spike-pulse 2s ease-in-out infinite',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        'spike-pulse': {
          '0%, 100%': { borderColor: 'rgba(192,57,43,0.3)' },
          '50%': { borderColor: 'rgba(192,57,43,0.9)' },
        },
      },
    },
  },
  plugins: [],
}
