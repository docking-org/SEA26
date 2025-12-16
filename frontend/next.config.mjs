/** @type {import('next').NextConfig} */
const nextConfig = {
    // output: 'export',
    reactStrictMode: true,
    images: {
        unoptimized: true,
    },
    distDir: 'out',
    rewrites: async () => {
        return [
            {
                source: '/api/:path*',
                destination: 'http://127.0.0.1:5050/api/:path*' // Proxy to Backend
            },
        ]
    }
};

export default nextConfig;