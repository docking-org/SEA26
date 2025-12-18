import { useState } from "react";



export default function NavBar() {
    const [menuOpen, setMenuOpen] = useState(false);


    return (
        <nav className="top-0 w-full z-50 transition-all duration-300 bg-slate hover:bg-white/20 backdrop-blur-md">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <div className="flex items-center justify-between h-14 sm:h-16 md:h-20"> 
                    <div className="shrink-0">
                        <img src="/wave.png" alt="SEA Logo" className="h-6 w-6 sm:w-8 sm:h-8 inline-block mr-2" />
                        <a href="/" className="text-teal-100 text-lg sm:text-xl md:text-2xl font-medium">SEA</a>
                    </div>
                    {/* Navigation Links */}
                    <div className="hidden md:flex">
                        <div className="ml-10 flex items-center space-x-6 lg:space-x-8">
                            <a href="/recentTasks" className="text-gray-900 hover:text-teal-800 px-3 py-2 rounded-md text-sm font-medium">Recent Jobs</a>
                        </div>
                        </div>
                    <button className="md:hidden p-2 text-2xl"
                        onClick={() => setMenuOpen((prev) => !prev)}
                    >
                        {menuOpen ? ( 
                            <svg className="w-6 h-6 sm:w-6 sm:h-6 text-white"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                xmlns="http://www.w3.org/2000/svg">
                                <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    strokeWidth={2}
                                    d="M6 18L18 6M6 6l12 12"
                                />
                            </svg>
                        ) : (
                        <svg className="w-6 h-6 sm:w-6 sm:h-6 text-white"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 20 20"
                            xmlns="http://www.w3.org/2000/svg">
                            <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M4 6h16M4 12h16M4 18h16"
                                />
                            </svg>
                        )}
                    </button>
                </div>
            </div>
            {menuOpen && (
                <div className="md:hidden bg-slate-800 px-2 pt-2 pb-3 space-y-1 text-right animate-in slide-down-from-top duration-300">
                    <a href="/recent_jobs" className="block text-gray-200 hover:text-white px-3 py-2 rounded-md text-base font-medium">Recent Jobs</a>
                    <a href="/contact" className="block text-gray-200 hover:text-white px-3 py-2 rounded-md text-base font-medium">Contact</a>
                </div>
            )}
        </nav>
    );
}
