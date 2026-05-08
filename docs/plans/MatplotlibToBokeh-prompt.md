Write the following documents for the replacement of matplotlib with Bokeh for responsive and interactive charts capable of handling thousands of vectors in a 2D space and structured to allow for 3D capability in the future. Features similar to those currently implemented using matplotlib should be maintained and optimized for Bokeh. This is especially important for hover tooltips showing vector attributes similar to those displayed by the current implementation.

The generated documents should be:
  1. A Product Requirements Document (PRD)
  2. An Implementation Plan (IP)
  3. A Task List (TL)

# Planning and Design Requirements
The PRD should be a markdown summary which can be used to generate an Implementation Plan or detailed prompts using AI tools. Do not include implementation details in the PRD unless specified here as an explicit implementation requirement.

The generated document sequence is: Product Requirements Document (PRD), Implementation Plan (IP), and Task List (TL).

The Implementation Plan should include a layout of the GUI for displaying the plots and including pull downs for changing views, styles, colors, and other plot parameters.

In all generated documentation, include approval sections that asks the user to approve the generated document before the next document in the sequence is generated.

Include a completion check box for each task in the Task List. A phase cannot be marked as complete until all tasks in the phase are marked as complete.

Include instructions in the Product Requirements Document (PRD) for generating the corresponding Implementation Plan (IP) once the PRD has been approved. Similarly, include instructions in the Implementation Plan (IP) for generating a Task List (TL) which can be delegated to subagents. Implementation can not begin until the Implementation Plan (IP) has been approved and the corresponding Task List (TL) has been approved by the user.

Approval can be revoked at any time by the user. If approval is revoked for any document, begin the process again with the generation of a new document. Approval for subsequent documents is contingent on the approval of previous documents in the sequence.

Do NOT begin implementation until all of the above plans have been approved and you are explicitly told to do so.

# Implementation Requirements
A key requirement is the complete removal of all matplotlib references from the source code and documentation and replaced as needed with Bokeh references.

An implementation requirement is that a python virtual environment named .venv-bokeh for Bokeh plotting should be created in the project root directory and all dependencies installed in that virtual environment. All invocations of python will first activate this environment. Note: This is a long-running development task and the virtual environment may need to be recreated in the future.

# Visualization Requirements
Visualization is activated when the --plot-moves option is used.

The visualizations should be rendered using a web page framework, which will be served locally and displayed using a new browser window. Additional browser tabs will be used to display additional views of the vector data using Bokeh charts. Each tab will contain a view of the data selected by the user with the first tab displaying all of the vectors and subsequent tabs displaying subsets of the data.

Each view will be comprised of three charts:
  - A main X-Y chart displaying all of the selected vectors. Allow for future enhancement and implementation of the Z-axis. This plot should support zooming, panning, and rotation for 3D enhanced visualization.
  - A histogram of the power settings for the selected vectors.
  - A histogram of the speed settings for the selected vectors.

Each view will include a menu bar for file operations and settings specific to that view.

## Required UI Features
  - The tabs should be used to switch between different views of the same data.
  - Hovering over a vector in a view should display a tooltip showing the vector attributes. The exact format of the tooltip is up to the implementer but it should be similar in format and contain the same information as the tooltip displayed by the current matplotlib implementation.
  - Include code to remap the mouse buttons for multi-button mouse support. The user normally uses the following buttons:Left button - select vectors; Middle button - pan the view; Right button - open context menu.
  - The left mouse button can be used to draw a box to select multiple vectors. The box should be drawn with a dashed line and the vectors inside the box should be highlighted.
  - Controls should be available to zoom (using the mouse scroll wheel), pan (using the center mouse button). Zooming using the mouse wheel should automatically center the view around the position of the mouse pointer.
  - Right clicking on a vector in a view should open a context menu that allows the user to open a new tab (view) with a copy of the current view's settings but with the selected vector as the first vector displayed in the new view's plot, and all other settings the same.
  - Right clicking away from any vectors should open a context menu that allows the user to open a new tab (view) with a copy of the current view's settings. The new view (tab) should display all vectors in the same order and grouping as the original view.
  - Settings should include options for changing the plot parameters and saving the plots. These settings should be located in the menu bar for that view.
  - Each view should be capable of being saved as an image (png or jpg), a vector file (e.g. SVG), and as a standalone html file.
  - Each view should also have a reset button that resets the view to the original settings of the view. This button should be located in the menu bar for that view.
  - Each view should also have a slider or some control to limit the number of vectors displayed to a range of values (start value and number of vectors to display, where start value is 0-based relative to the first vector in the current view) and update the plot to display only the selected vectors. This control should be located in the menu bar for that view.

  ### Advanced Filtering
  - A searchable pull-down located in the menu bar for that view displays a list of commands that were decoded and allows the user to select a command to serve as the start of a new view in a separate tab. The pull-down menu should be updated in real time as new commands are decoded.
	  - The mouse scroll wheel is used to scroll the list inside the pull down.
	  - The user should be able to filter the vectors by type (e.g. move or cut), color, speed, and power percentage and update the plot to display only the selected vectors as they would normally appear. Unselected vectors should be displayed with a lower opacity (configurable).
	  - Hovering over a command in the pull-down should display a summary of that command in a tool-tip like display area.
	  - Hovering over an item in the summary should highlight the actual vector in the associated view(s) using some visual indication such as a highlight box or color change.
	  - Right clicking on an item in the summary should open a context menu that allows the user to open a new tab with a copy of the current view's settings but with the selected item as the first vector displayed and all other settings the same.
	  - The display of a single command should be the same as the display of a single command in the current implementation as seen in the decoded output text file. It should include the command name, the command code, the command parameters, and any other information that is currently displayed.

## Special Considerations
  - When visualization is activated (via --plot-moves option) and command decoding has completed, the CLI will automatically enter an interactive mode instead of exiting and will display a "Now plotting moves. Close browser window to exit." message.
  - Saving Plots Natively: Use the built-in GUI save plot functionality to save as an image file (png or jpg).
  - Terminal CLI Interaction: With the exception of command filtering, CLI behavior should be preserved. The CLI should be able to pause for input while the plot remains interactive in the GUI. When the CLI steps through commands, it should push data updates to the GUI in a thread safe manner to ensure that the GUI remains responsive.
  - Terminal CLI Commands: Update the CLI commands to work with the new plotting system.
  - On the fly: Because the Bokeh GUI plot will be launched in a browser and the user will interact with it there, the --on-the-fly option restrictions can be removed. The --on-the-fly option should then simply enable real-time plotting for commands as they are decoded. The main plot (first view) should be updated as each new vector is decoded.
  - Virtual Environment Setup: The creation of the .venv-bokeh virtual environment for Bokeh plotting should be handled automatically using a setup script and all other Bokeh related setup should be handled automatically, including the installation of all dependencies. Provide clear instructions in the Implementation Plan on how to set up the virtual environment and install the dependencies and any other setup information required.
  - Update the usage messages to remove the --on-the-fly option restrictions and to reflect the new --on-the-fly behavior.

## Testing
Provide information in the Implementation Plan on how to test the Bokeh plotting system to ensure that it meets all of the requirements. This section will be used by subagents to develop the Task List. Test code will be placed in a directory named tests/bokeh-plotting.

# Additional Instructions
Ask any questions you may have while developing the PRD and I will answer them to ensure that the PRD is complete and accurate.

## File and Directory Naming Conventions
- Save the PRD as a markdown file in a subdirectory of the docs/plans directory named bokeh_plotting-<llm_name> where llm_name is the name of the LLM that generated the document. Replace any spaces in the LLM name with hyphens. The directory should be created if it doesn't exist.
- The base file name is bokeh_plotting_<DOC_TYPE>.md where <DOC_TYPE> is replaced with PRD for Product Requirements Document, IP for Implementation Plan, and TL for Task List.

