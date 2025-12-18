import NavBar from "./NavBar"
import Footer from "./Footer";


export const metadata = {
    title: "SEA - Similarity Ensemble Approach",
    description: "The Similarity Ensemble Approach (SEA) relates proteins based on the set-wise chemical similarity among their ligands.",
  };

  
export default function Layout({ children }) {
    return (
      <div className="flex flex-col min-h-screen bg-[url('/src/assets/sea-bg.jpg')] bg-cover min-h-screen bg-slate-950 text-white overflow-hidden">
        <NavBar />
            <main className="flex-grow container mx-auto px-4 py-10">
              <div className="bg-white/10 p-6 rounded-lg shadow-lg backdrop-blur-md border border-white/20">
                {children}
              </div>
            </main>
        <Footer />
      </div>
    )
  }