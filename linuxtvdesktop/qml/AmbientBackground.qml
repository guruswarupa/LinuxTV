import QtQuick 2.15

// A soft, slow-crossfading wash behind the whole screen that follows the
// focused app's brand color (and its blurred icon, when one is available).
Item {
    id: root
    property color targetColor: theme.bg
    property string targetBackdrop: ""
    property bool reduced: false

    Rectangle {
        anchors.fill: parent
        color: theme.bg
    }

    Rectangle {
        anchors.fill: parent
        color: root.targetColor
        opacity: 0.55
        Behavior on color { ColorAnimation { duration: 400 } }
    }

    Image {
        anchors.fill: parent
        source: root.reduced ? "" : root.targetBackdrop
        fillMode: Image.PreserveAspectCrop
        asynchronous: true
        opacity: (root.reduced || root.targetBackdrop === "") ? 0 : 0.35
        Behavior on opacity { NumberAnimation { duration: 400 } }
    }

    // Vignette so foreground text and cards stay legible over any backdrop.
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.rgba(0, 0, 0, 0.15) }
            GradientStop { position: 0.55; color: Qt.rgba(0, 0, 0, 0.45) }
            GradientStop { position: 1.0; color: Qt.rgba(0, 0, 0, 0.85) }
        }
    }
}
