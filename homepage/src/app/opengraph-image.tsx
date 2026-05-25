import { ImageResponse } from 'next/og'

export const alt = 'DECIFER Trading — Market Decision Intelligence'
export const size = { width: 1200, height: 630 }
export const contentType = 'image/png'

export default async function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          background: '#070a12',
          width: '100%',
          height: '100%',
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          padding: '72px 80px',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {/* Subtle grid */}
        <div
          style={{
            position: 'absolute',
            inset: 0,
            backgroundImage:
              'linear-gradient(rgba(240,90,40,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(240,90,40,0.04) 1px, transparent 1px)',
            backgroundSize: '72px 72px',
          }}
        />

        {/* Orange glow top-right */}
        <div
          style={{
            position: 'absolute',
            top: -120,
            right: -120,
            width: 480,
            height: 480,
            background: 'radial-gradient(circle, rgba(240,90,40,0.12) 0%, transparent 70%)',
            borderRadius: '50%',
          }}
        />

        {/* Top: brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, zIndex: 1 }}>
          <div
            style={{
              color: '#F05A28',
              fontSize: 26,
              fontWeight: 800,
              letterSpacing: '0.14em',
            }}
          >
            DECIFER
          </div>
          <div
            style={{
              width: 4,
              height: 4,
              borderRadius: '50%',
              background: 'rgba(240,90,40,0.5)',
            }}
          />
          <div
            style={{
              color: 'rgba(255,255,255,0.5)',
              fontSize: 22,
              fontWeight: 500,
              letterSpacing: '0.08em',
            }}
          >
            TRADING
          </div>
        </div>

        {/* Centre: headline */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20, zIndex: 1 }}>
          <div
            style={{
              color: '#ffffff',
              fontSize: 72,
              fontWeight: 700,
              lineHeight: 1.06,
              letterSpacing: '-0.02em',
            }}
          >
            Market Decision
            <br />
            <span
              style={{
                background: 'linear-gradient(90deg, #F05A28, #f47040)',
                WebkitBackgroundClip: 'text',
                color: 'transparent',
              }}
            >
              Intelligence.
            </span>
          </div>
          <div
            style={{
              color: 'rgba(255,255,255,0.4)',
              fontSize: 26,
              fontWeight: 400,
              lineHeight: 1.4,
              maxWidth: 640,
            }}
          >
            The missing layer between market noise and investor action.
          </div>
        </div>

        {/* Bottom: signals + domain */}
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'flex-end',
            zIndex: 1,
          }}
        >
          <div style={{ display: 'flex', gap: 10 }}>
            {['Macro', 'Catalysts', 'Regime', 'Portfolio'].map((label) => (
              <div
                key={label}
                style={{
                  background: 'rgba(240,90,40,0.08)',
                  border: '1px solid rgba(240,90,40,0.2)',
                  borderRadius: 6,
                  padding: '7px 16px',
                  color: 'rgba(240,90,40,0.9)',
                  fontSize: 17,
                  fontWeight: 600,
                  letterSpacing: '0.03em',
                }}
              >
                {label}
              </div>
            ))}
          </div>
          <div style={{ color: 'rgba(255,255,255,0.2)', fontSize: 19, fontWeight: 500 }}>
            decifertrading.com
          </div>
        </div>
      </div>
    ),
    { ...size }
  )
}
