/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          DEFAULT: '#1e2128',
          soft: '#736f60',
          line: 'rgba(30,33,40,0.12)',
        },
        paper: {
          DEFAULT: '#f6f1e4',
          dim: '#ece4cf',
        },
        surface: '#fffdf7',
        brass: {
          DEFAULT: '#c68a3d',
          dim: '#a3702f',
        },
        moss: '#5c7a4c',
        rust: '#a3432e',
      },
      fontFamily: {
        display: ['var(--font-display)', 'serif'],
        body: ['var(--font-body)', 'sans-serif'],
        mono: ['var(--font-mono)', 'monospace'],
      },
      boxShadow: {
        folio: '0 1px 0 rgba(30,33,40,0.03), 0 10px 20px -12px rgba(30,33,40,0.22)',
      },
    },
  },
  plugins: [],
}
