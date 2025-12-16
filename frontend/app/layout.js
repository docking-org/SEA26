import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

import NavBar from "./layout/NavBar";
import Footer from "./layout/Footer";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata = {
  title: "SEA - Similarity Ensemble Approach",
  description: "The Similarity Ensemble Approach (SEA) relates proteins based on the set-wise chemical similarity among their ligands.",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased`}
      >
          <div className="content bg-[url('/sea.jpg')] bg-cover min-h-full text-white">
              <NavBar />
              {children}
              <Footer />
        </div>
      </body>
    </html>
  );
}
