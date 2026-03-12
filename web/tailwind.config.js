/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      // ============================================
      // COLOR SYSTEM - DaisyUI + Tailwind 扩展
      // ============================================
      colors: {
        primary: {
          50: '#eef2ff',
          100: '#e0e7ff',
          200: '#c7d2fe',
          300: '#a5b4fc',
          400: '#818cf8',
          500: '#6366f1',
          600: '#4f46e5',
          700: '#4338ca',
          800: '#3730a3',
          900: '#312e81',
        },
      },

      // ============================================
      // TYPOGRAPHY SYSTEM
      // ============================================
      fontFamily: {
        // 主字体：西文 Inter + 中文 Noto Sans SC
        sans: ['Inter', 'Noto Sans SC', 'system-ui', '-apple-system', 'sans-serif'],
        // 等宽字体：代码和数据显示
        mono: ['JetBrains Mono', 'Fira Code', 'SF Mono', 'Monaco', 'monospace'],
        // 展示字体：标题和特殊场景
        display: ['Inter', 'Noto Sans SC', 'system-ui', 'sans-serif'],
      },

      fontSize: {
        // 基础字体大小（基于 16px = 1rem）
        '2xs': ['0.625rem', { lineHeight: '0.875rem' }],    // 10px - 标签、徽章
        'xs': ['0.75rem', { lineHeight: '1rem' }],          // 12px - 辅助文字
        'sm': ['0.875rem', { lineHeight: '1.25rem' }],      // 14px - 正文小号
        'base': ['1rem', { lineHeight: '1.5rem' }],         // 16px - 正文
        'lg': ['1.125rem', { lineHeight: '1.75rem' }],      // 18px - 大正文
        'xl': ['1.25rem', { lineHeight: '1.75rem' }],       // 20px - 小标题
        '2xl': ['1.5rem', { lineHeight: '2rem' }],          // 24px - 标题
        '3xl': ['1.875rem', { lineHeight: '2.25rem' }],     // 30px - 大标题
        '4xl': ['2.25rem', { lineHeight: '2.5rem' }],       // 36px - 展示标题
      },

      fontWeight: {
        light: '300',
        normal: '400',
        medium: '500',
        semibold: '600',
        bold: '700',
      },

      lineHeight: {
        none: '1',
        tight: '1.25',
        snug: '1.375',
        normal: '1.5',
        relaxed: '1.625',
        loose: '2',
      },

      letterSpacing: {
        tighter: '-0.05em',
        tight: '-0.025em',
        normal: '0',
        wide: '0.025em',
        wider: '0.05em',
        widest: '0.1em',
      },

      // ============================================
      // SPACING SYSTEM (4px base unit)
      // ============================================
      spacing: {
        '0': '0',
        '0.5': '0.125rem',   // 2px
        '1': '0.25rem',      // 4px
        '1.5': '0.375rem',   // 6px
        '2': '0.5rem',       // 8px
        '2.5': '0.625rem',   // 10px
        '3': '0.75rem',      // 12px
        '3.5': '0.875rem',   // 14px
        '4': '1rem',         // 16px
        '5': '1.25rem',      // 20px
        '6': '1.5rem',       // 24px
        '7': '1.75rem',      // 28px
        '8': '2rem',         // 32px
        '9': '2.25rem',      // 36px
        '10': '2.5rem',      // 40px
        '11': '2.75rem',     // 44px
        '12': '3rem',        // 48px
        '14': '3.5rem',      // 56px
        '16': '4rem',        // 64px
        '20': '5rem',        // 80px
        '24': '6rem',        // 96px
        '28': '7rem',        // 112px
        '32': '8rem',        // 128px
        '36': '9rem',        // 144px
        '40': '10rem',       // 160px
        '44': '11rem',       // 176px
        '48': '12rem',       // 192px
        '52': '13rem',       // 208px
        '56': '14rem',       // 224px
        '60': '15rem',       // 240px
        '64': '16rem',       // 256px
        '72': '18rem',       // 288px
        '80': '20rem',       // 320px
        '96': '24rem',       // 384px
      },

      // ============================================
      // LAYOUT - Container & Breakpoints
      // ============================================
      container: {
        center: true,
        padding: {
          DEFAULT: '1rem',
          sm: '1.5rem',
          lg: '2rem',
          xl: '3rem',
        },
      },

      screens: {
        'xs': '480px',       // 小屏手机
        'sm': '640px',       // 手机横屏
        'md': '768px',       // 平板
        'lg': '1024px',      // 小桌面
        'xl': '1280px',      // 桌面
        '2xl': '1536px',     // 大桌面
      },

      // ============================================
      // BORDER RADIUS
      // ============================================
      borderRadius: {
        'none': '0',
        'sm': '0.25rem',     // 4px
        'DEFAULT': '0.375rem', // 6px
        'md': '0.5rem',      // 8px
        'lg': '0.75rem',     // 12px
        'xl': '1rem',        // 16px
        '2xl': '1.25rem',    // 20px
        '3xl': '1.5rem',     // 24px
        'full': '9999px',
      },

      // ============================================
      // SHADOWS
      // ============================================
      boxShadow: {
        'sm': '0 1px 2px 0 rgb(0 0 0 / 0.05)',
        'DEFAULT': '0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)',
        'md': '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
        'lg': '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
        'xl': '0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)',
        '2xl': '0 25px 50px -12px rgb(0 0 0 / 0.25)',
        'inner': 'inset 0 2px 4px 0 rgb(0 0 0 / 0.05)',
        'glow': '0 0 20px hsl(var(--p) / 0.3)',
        'glow-accent': '0 0 20px hsl(var(--a) / 0.3)',
      },

      // ============================================
      // TRANSITIONS
      // ============================================
      transitionDuration: {
        '75': '75ms',
        '100': '100ms',
        '150': '150ms',
        '200': '200ms',
        '300': '300ms',
        '500': '500ms',
        '700': '700ms',
        '1000': '1000ms',
      },

      transitionTimingFunction: {
        'DEFAULT': 'cubic-bezier(0.4, 0, 0.2, 1)',
        'linear': 'linear',
        'in': 'cubic-bezier(0.4, 0, 1, 1)',
        'out': 'cubic-bezier(0, 0, 0.2, 1)',
        'in-out': 'cubic-bezier(0.4, 0, 0.2, 1)',
        'bounce': 'cubic-bezier(0.68, -0.55, 0.265, 1.55)',
      },

      // ============================================
      // Z-INDEX
      // ============================================
      zIndex: {
        '0': '0',
        '10': '10',
        '20': '20',
        '30': '30',
        '40': '40',
        '50': '50',
        'modal': '100',
        'popover': '200',
        'tooltip': '300',
        'toast': '400',
        'max': '9999',
      },

      // ============================================
      // ANIMATION
      // ============================================
      animation: {
        'fade-in': 'fadeIn 0.3s ease-out',
        'fade-out': 'fadeOut 0.2s ease-in',
        'slide-in-right': 'slideInRight 0.3s ease-out',
        'slide-in-up': 'slideInUp 0.3s ease-out',
        'slide-in-down': 'slideInDown 0.3s ease-out',
        'pulse-soft': 'pulseSoft 2s ease-in-out infinite',
        'bounce-soft': 'bounceSoft 1s ease-in-out infinite',
        'spin-slow': 'spin 3s linear infinite',
      },

      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(10px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeOut: {
          '0%': { opacity: '1' },
          '100%': { opacity: '0' },
        },
        slideInRight: {
          '0%': { opacity: '0', transform: 'translateX(20px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
        slideInUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideInDown: {
          '0%': { opacity: '0', transform: 'translateY(-20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        pulseSoft: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.7' },
        },
        bounceSoft: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-5px)' },
        },
      },

      // ============================================
      // GRID
      // ============================================
      gridTemplateColumns: {
        'auto-fit': 'repeat(auto-fit, minmax(0, 1fr))',
        'auto-fill': 'repeat(auto-fill, minmax(0, 1fr))',
      },
    },
  },

  plugins: [
    require('daisyui'),
  ],

  // ============================================
  // DAISYUI THEME CONFIG
  // ============================================
  daisyui: {
    themes: [
      {
        tlonDark: {
          // Brand Colors
          primary: '#6366f1',
          'primary-content': '#ffffff',
          secondary: '',
          'secondary-content': '#e2e8f0',
          accent: '#10b981',
          'accent-content': '#ffffff',
          
          // Neutral Colors
          neutral: '#1e293b',
          'neutral-content': '#cbd5e1',
          
          // Base Colors (Background layers)
          'base-100': '#0f172a',  // 最底层背景
          'base-200': '#1e293b',  // 第二层背景
          'base-300': '',  // 第三层背景
          'base-content': '#f1f5f9',
          
          // Semantic Colors
          info: '#3b82f6',
          'info-content': '#ffffff',
          success: '#10b981',
          'success-content': '#ffffff',
          warning: '#f59e0b',
          'warning-content': '',
          error: '#ef4444',
          'error-content': '#ffffff',
          
          // Border Radius (全局默认)
          '--rounded-box': '0.75rem',      // 12px - 卡片、容器
          '--rounded-btn': '0.5rem',       // 8px - 按钮
          '--rounded-badge': '9999px',     // 完全圆角 - 徽章
          '--rounded-card': '1rem',        // 16px - 大卡片
          
          // Typography Scale
          '--font-size-xs': '0.75rem',
          '--font-size-sm': '0.875rem',
          '--font-size-base': '1rem',
          '--font-size-lg': '1.125rem',
          '--font-size-xl': '1.25rem',
          '--font-size-2xl': '1.5rem',
        },
        
        tlonLight: {
          primary: '#4f46e5',
          'primary-content': '#ffffff',
          secondary: '#e2e8f0',
          'secondary-content': '',
          accent: '',
          'accent-content': '#ffffff',
          neutral: '#f1f5f9',
          'neutral-content': '#1e293b',
          'base-100': '#ffffff',
          'base-200': '#f8fafc',
          'base-300': '#e2e8f0',
          'base-content': '#0f172a',
          info: '#2563eb',
          'info-content': '#ffffff',
          success: '',
          'success-content': '#ffffff',
          warning: '#d97706',
          'warning-content': '',
          error: '#dc2626',
          'error-content': '#ffffff',
          '--rounded-box': '0.75rem',
          '--rounded-btn': '0.5rem',
          '--rounded-badge': '9999px',
          '--rounded-card': '1rem',
          '--font-size-xs': '0.75rem',
          '--font-size-sm': '0.875rem',
          '--font-size-base': '1rem',
          '--font-size-lg': '1.125rem',
          '--font-size-xl': '1.25rem',
          '--font-size-2xl': '1.5rem',
        },
      },
    ],
    darkTheme: 'tlonDark',
    base: true,
    styled: true,
    utils: true,
    prefix: '',
    logs: true,
    themeRoot: ':root',
  },
}
