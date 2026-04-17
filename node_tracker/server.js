const express = require('express');
const http = require('http');
const socketIo = require('socket.io');
const mongoose = require('mongoose');
const cors = require('cors');
const path = require('path');
const User = require('./models/User');
const Project = require('./models/Project');

const app = express();
const server = http.createServer(app);
const io = socketIo(server);

app.use(cors());
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Connect to MongoDB
mongoose.connect('mongodb://127.0.0.1:27017/project_tracker', {
}).then(() => console.log('MongoDB Connected'))
  .catch(err => console.log(err));

// API Routes
app.post('/api/login', async (req, res) => {
  const { username, password } = req.body;
  let user = await User.findOne({ username });
  if (!user) {
    if(username === 'admin') {
      user = await User.create({ username, password, role: 'admin' });
    } else {
      user = await User.create({ username, password, role: 'user' });
    }
  }
  if (user.password === password) {
    res.json({ success: true, user: { id: user._id, username: user.username, role: user.role } });
  } else {
    res.json({ success: false, message: 'Invalid password' });
  }
});

app.get('/api/projects', async (req, res) => {
  const { userId, role } = req.query;
  try {
    let projects;
    if (role === 'admin') {
      projects = await Project.find().populate('owner', 'username').sort({ updatedAt: -1 });
    } else {
      projects = await Project.find({ owner: userId }).populate('owner', 'username').sort({ updatedAt: -1 });
    }
    res.json(projects);
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/projects', async (req, res) => {
  const { name, description, ownerId } = req.body;
  const project = new Project({ name, description, owner: ownerId });
  await project.save();
  await project.populate('owner', 'username');
  res.json(project);
});

// Socket.IO
io.on('connection', (socket) => {
  console.log('New client connected: ' + socket.id);

  socket.on('join_dashboard', (userId) => {
    socket.join(userId);
    console.log(`User ${userId} joined their personal room`);
  });

  socket.on('join_admin', () => {
    socket.join('admin_room');
    console.log(`An admin joined the admin room`);
  });

  socket.on('project_created', (data) => {
    // Notify admins
    io.to('admin_room').emit('new_project', data);
  });

  socket.on('update_project_status', async (data) => {
    const { projectId, status } = data;
    try {
      const project = await Project.findByIdAndUpdate(projectId, { status }, { new: true }).populate('owner', 'username');
      
      // Notify the specific user
      io.to(project.owner._id.toString()).emit('project_updated', {
        projectId,
        status,
        message: `Your project "${project.name}" is now ${status}.`
      });
      
      // Update admins too
      io.to('admin_room').emit('project_updated', {
        projectId,
        status,
        project
      });
    } catch (err) {
      console.log(err);
    }
  });

  socket.on('disconnect', () => {
    console.log('Client disconnected: ' + socket.id);
  });
});

const PORT = process.env.PORT || 3000;
server.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
