import { useState, useEffect } from "react";
import Layout from "./components/Layout";

export default function RecentTasks() {
    const [tasks, setTasks] = useState([]);

    function fetchRecentTasks() {
        // Fetch recent tasks from backend API
        fetch('/api/recent_tasks')
            .then(response => response.json())
            .then(data => setTasks(data.recent_tasks))
            .catch(error => console.error('Error fetching recent tasks:', error));
    }

    useEffect(() => {
        fetchRecentTasks();
    }, []);



    return (
        <Layout>
        <div className="px-2 sm:px-0">
            <h1 className="text-xl sm:text-2xl font-bold mb-4">Recent Tasks</h1>
                <div className="overflow-x-auto">
                    <table className="min-w-full bg-white border text-gray-950 border-white/20 rounded-lg">
                        <thead>
                            <tr>
                                <th className="py-2 px-2 sm:px-4 border-b border-gray-300 text-left text-sm sm:text-base">Task ID</th>
                                <th className="py-2 px-2 sm:px-4 border-b border-gray-300 text-left text-sm sm:text-base">Status</th>
                                <th className="py-2 px-2 sm:px-4 border-b border-gray-300 text-left text-sm sm:text-base">Submitted At</th>
                            </tr>
                        </thead>
                        <tbody>
                            {tasks.map((task) => (
                                <tr key={task.task_id} className="hover:bg-white/20">
                                    <td className="py-2 px-2 sm:px-4 border-b border-gray-300 text-xs sm:text-sm break-all sm:break-normal">
                                        <a className="text-teal-800 hover:underline"
                                        href={`/result?taskId=${task.task_id}`}>{task.task_id}</a>
                                    </td>
                                    <td className="py-2 px-2 sm:px-4 border-b border-gray-300 text-xs sm:text-sm">
                                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-medium ${
                                            task.status === 'SUCCESS' ? 'bg-green-100 text-green-800' :
                                            task.status === 'FAILED' ? 'bg-red-100 text-red-800' :
                                            task.status === 'RUNNING' ? 'bg-blue-100 text-blue-800' :
                                            task.status === 'PENDING' ? 'bg-yellow-100 text-yellow-800' :
                                            'bg-gray-100 text-gray-800'
                                        }`}>
                                            {task.status}
                                        </span>
                                    </td>
                                    <td className="py-2 px-2 sm:px-4 border-b border-gray-300 text-xs sm:text-sm whitespace-nowrap">{task.time_stamp}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
                </div>
            </Layout>
     
    );
    }