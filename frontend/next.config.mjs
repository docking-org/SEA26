/** @type {import('next').NextConfig} */
const nextConfig = {
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
