import React from 'react'

const Footer = () => {
  // get current year
  const currentYear = new Date().getFullYear();

  return (
    <div className='w-full relative text-center p-4 bg-transparent backdrop-blur-md'>
        <p className='text-gray-200'>© 2007-{currentYear} SEA. All rights reserved.</p>
        <p className="text-muted text-sm/tight">This is not an official UCSF website. The opinions or statements expressed herein should not be taken as a position of or endorsement by the University of California, San Francisco.</p>
    </div>
  )
}
export default Footer