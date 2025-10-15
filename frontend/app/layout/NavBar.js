'use client';
import React from 'react'
import {useState}  from 'react'
import Link from 'next/link'
import Image from 'next/image'
import wave from '../../public/wave.png'


const Navigation = () => {
    const [menuOpen, setMenuOpen] = useState(false)

    const toggleMenu = () => {
        setMenuOpen(!menuOpen)
    }
  return (
    <div className='w-full top-0 left-0 z-50'>
      <nav className='flex justify-between px-5 items-center z-30 py-5 bg-transparent shadow-xl backdrop-blur-sm'>
        <Link href="/">
          <Image 
              src={wave}
              alt="sea_logo.png"
              width={50}
              height={50}
              className='cursor-pointer'
              priority />
        </Link>
        {/* <Link href="/" className='text-2xl font-bold text-blue-900 dark:text-white'>
        </Link> */}
        {/* <ul className='hidden md:flex items-center space-x-8'>
          {NAV_LINKS.map((link) => (
            <Link key={link.href} href={link.href} className="text-gray-950 hover:text-blue-600 dark:text-blue-100 dark:hover:text-white">
              {link.label}
            </Link>
          ))}
          {/* <div className="md:flex">
            <Link

              href="/search">
              <Button 
                type='button'
                title='Run SEA'
                icon='window.svg'
                variant='blue'
              />
            </Link>
          </div> */}
        {/* </ul> */}
      
        <div className="md:hidden flex items-center">
          <button onClick={toggleMenu} className="focus:outline-none">
            {/* Hamburger icon */}
            <svg
              className="w-6 h-6 text-gray-800"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 20 20"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 6h16M4 12h16M4 18h16"
              />
            </svg>
          </button>
        
        </div>
    
      </nav>
      {menuOpen && (
            <div className="relative right-0 width-100  bg-white border border-gray-200 rounded-md shadow-lg">
              <ul className="py-2">
                {NAV_LINKS.map((link) => (
                  <li key={link.href}>
                    <Link
                      href={
                        link.href
                      }
                      className="block px-4 py-2 text-gray-800 hover:bg-gray-100"
                      onClick={() => setMenuOpen(false)} // Close menu on link click
                    >
                      {link.label}
                    </Link>
                  </li>
                ))}
                <li>
                  <Link
                    href="/search"
                    className="block px-4 py-2 text-gray-800 hover:bg-gray-100"
                    onClick={() => setMenuOpen(false)} // Close menu on link click
                  >
                    Run SEA
                  </Link>
                </li>
              </ul>
            </div>
          )}
    </div>
  )
}

export default Navigation