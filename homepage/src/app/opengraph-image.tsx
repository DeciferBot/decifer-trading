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
        }}
      >
        {/* Top: brand */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div
            style={{
              color: '#F05A28',
              fontSize: 26,
              fontWeight: 800,
              letterSpacing: '0.14em',
              display: 'flex',
            }}
          >
            DECIFER
          </div>
          <div
            style={{
              width: 5,
              height: 5,
              borderRadius: '50%',
              background: 'rgba(240,90,40,0.5)',
              display: 'flex',
            }}
          />
          <div
            style={{
              color: 'rgba(255,255,255,0.45)',
              fontSize: 22,
              fontWeight: 500,
              letterSpacing: '0.08em',
              display: 'flex',
            }}
          >
            TRADING
          </div>
        </div>

        {/* Centre: headline */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 0,
            }}
          >
            <span
              style={{
                color: '#ffffff',
                fontSize: 74,
                fontWeight: 700,
                lineHeight: 1.06,
                letterSpacing: '-0.02em',
                display: 'flex',
              }}
            >
              Market Decision
            </span>
            <span
              style={{
                color: '#F05A28',
                fontSize: 74,
                fontWeight: 700,
                lineHeight: 1.06,
                letterSpacing: '-0.02em',
                display: 'flex',
              }}
            >
              Intelligence.
            </span>
          </div>
          <div
            style={{
              color: 'rgba(255,255,255,0.38)',
              fontSize: 26,
              fontWeight: 400,
              maxWidth: 620,
              display: 'flex',
            }}
          >
            The missing layer between market noise and investor action.
          </div>
        </div>

        {/* Bottom: signal tags + domain */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
          <div style={{ display: 'flex', gap: 10 }}>
            {['Macro', 'Catalysts', 'Regime', 'Portfolio'].map((label) => (
              <div
                key={label}
                style={{
                  background: 'rgba(240,90,40,0.08)',
                  border: '1px solid rgba(240,90,40,0.2)',
                  borderRadius: 6,
                  padding: '7px 16px',
                  color: 'rgba(240,90,40,0.85)',
                  fontSize: 17,
                  fontWeight: 600,
                  letterSpacing: '0.03em',
                  display: 'flex',
                }}
              >
                {label}
              </div>
            ))}
          </div>
          <div style={{ color: 'rgba(255,255,255,0.2)', fontSize: 19, fontWeight: 500, display: 'flex' }}>
            decifertrading.com
          </div>
        </div>
      </div>
    ),
    { ...size }
  )
}
