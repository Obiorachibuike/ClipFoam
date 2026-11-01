/** @type {import('next').NextConfig} */
const rawTarget = process.env.API_PROXY_TARGET || process.env.NEXT_PUBLIC_API_PROXY_TARGET || 'http://127.0.0.1:8000';

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${rawTarget}/api/v1/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: '/(.*)',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        ],
      },
    ];
  },
};

export default nextConfig;
