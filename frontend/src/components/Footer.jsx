const Footer = () => {
    // get current year
    const currentYear = new Date().getFullYear();
  
    return (
      <div className='w-full relative text-center px-3 py-3 sm:p-4 bg-transparent backdrop-blur-lg'>
          <p className='text-teal-950 text-xs sm:text-sm'>© 2007-{currentYear} SEA. <a target="_blank" rel="noopener noreferrer"  href="https://irwinlab.compbio.ucsf.edu/"></a>Irwin and <a target="_blank" rel="noopener noreferrer"  href="https://bkslab.org/" >Shoichet</a> Labs and the UC Regents</p>
          <p className="text-xs sm:text-sm"><a  rel="noopener noreferrer" href="https://www.nigms.nih.gov/">Supported by NIGMS via GM71896</a>.  <a  rel="noopener noreferrer" href="https://forms.gle/LZV1FCmLWxUWznVi9">Contact Us</a>  <a  rel="noopener noreferrer" href="https://sea.docking.org/stats/">Usage</a>  <a  rel="noopener noreferrer" href="https://wiki.docking.org/index.php?title=SEA"></a>Wiki  Terms of Use  Privacy policy</p>
          <p className="text-muted text-[10px] sm:text-sm/tight text-teal-950">This is not an official UCSF website. The opinions or statements expressed herein should not be taken as a position of or endorsement by the University of California, San Francisco.</p>
      </div>
    )
  }
  export default Footer