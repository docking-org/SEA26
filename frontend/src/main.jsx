import React, { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import ReactGA from 'react-ga4';
import './index.css'

// import pages
import HomePage from "./HomePage";
import Result from './SeaResults';
import RecentTasks from './RecentTasks';
import CompoundVsTarget from './CompoundVSTarget';
import NotFound from './NotFound';
import { AuthProvider } from './auth';


// Initialize Google Analytics
ReactGA.initialize('G-BBEC39W7VB');


const router = createBrowserRouter([
  {
    path: "/",
    element: <HomePage />,

  },
  {
    path: "/result",
    element: <Result />,
  },
  {
    path: "/recentTasks",
    element: <RecentTasks />,
  },
  {
    path:"/compoundVStarget",
    element : <CompoundVsTarget />,
  },
  {
    path: "*",
    element: <NotFound />,
  }

]);

ReactDOM.createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  </StrictMode>
);
