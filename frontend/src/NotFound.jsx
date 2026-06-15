import { Link } from "react-router-dom";
import Layout from "./components/Layout";

export default function NotFound() {
  return (
    <Layout>
      <div className="text-center py-20">
        <h1 className="text-6xl font-bold mb-4">404</h1>
        <p className="text-xl mb-8">Page not found</p>
        <Link
          to="/"
          className="px-6 py-3 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
        >
          Go Home
        </Link>
      </div>
    </Layout>
  );
}
