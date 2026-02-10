import React, { StrictMode } from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import './index.css'

// import pages
import HomePage from "./HomePage";
import Result from './SeaResults';
import RecentTasks from './RecentTasks';
import CompoundVsTarget from './CompoundVSTarget';


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
  }

]);

ReactDOM.createRoot(document.getElementById('root')).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
